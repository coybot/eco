"""A shareable web link for a mission's album.

The drone uploads each photo as it takes it. When the app shows an album it asks
POST /albums for a link: the server packs the original photos (EXIF intact) plus
the drone's position manifest into one zip beside them in the bucket, and returns

    https://<api>/prod/share/<random id>

which anyone can open - no app, no sign-in. Each open redirects to a freshly
presigned download of the zip, so the link itself does not expire after a day the
way a presigned URL does (Lambda credentials are temporary; a presigned URL dies
with them). It works for SHARE_DAYS: the bucket deletes every object 30 days after
upload (template.yaml, DeleteOldImages), so a longer link would outlive its zip.

The same album always gets the same link: the zip and the link are keyed by the
album's photo set, so asking again (another device, a reopened chat) is free.

Who may ask: the owner of the drone the photos came from. The drone id is read
from the photos' own keys (drones/<droneId>/...), so a request cannot borrow
someone else's photos by naming a different drone.
"""
import csv
import hashlib
import io
import json
import os
import secrets
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

import conversations as C

# How long a shared link works - no longer than the bucket keeps the zip.
SHARE_DAYS = int(os.environ.get('SHARE_DAYS', '30'))
# The presigned download a link redirects to; only needs to outlive the click.
DOWNLOAD_EXPIRES_S = 3600
# An album is a mission's photos; anything far bigger is not one.
MAX_PHOTOS = 500
PHOTO_EXTENSIONS = ('.jpg', '.jpeg', '.png')


def photo_key(url):
    """The bucket key a photo URL points at, or None.

    Accepts every form the app holds: presigned or plain S3
    (https://bucket.s3[.region].amazonaws.com/key?...), path-style S3
    (https://s3.region.amazonaws.com/bucket/key) and the ground station's
    (http://host:port/images/key?token=...)."""
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        return None
    parts = urlsplit(url)
    path = unquote(parts.path).lstrip('/')
    host = parts.hostname or ''
    if path.startswith('images/'):
        path = path[len('images/'):]
    elif host.startswith('s3.') or host.startswith('s3-'):
        path = path.split('/', 1)[1] if '/' in path else ''
    if not path or '..' in path.split('/'):
        return None
    return path


def album_drone_id(keys):
    """The one drone every key belongs to (drones/<id>/...), else None."""
    ids = {k.split('/')[1] for k in keys if k.startswith('drones/') and k.count('/') >= 2}
    if len(ids) != 1 or any(not k.startswith('drones/') for k in keys):
        return None
    return ids.pop()


def album_id(keys):
    """Stable id for a set of photos, whatever order the app lists them in."""
    return hashlib.sha256('\n'.join(sorted(set(keys))).encode('utf-8')).hexdigest()[:24]


def manifest_rows(manifest_texts, photo_names):
    """Rows of the drone's survey manifests that describe these photos.

    The drone writes one manifest per survey beside its photos; a conversation
    can hold several surveys, so keep only rows whose file is in this album.
    Returns (header, rows) - header None when no manifest mentions them."""
    header, rows = None, []
    wanted = set(photo_names)
    for text in manifest_texts:
        reader = csv.reader(io.StringIO(text))
        try:
            head = next(reader)
        except StopIteration:
            continue
        if 'file' not in head:
            continue
        col = head.index('file')
        for row in reader:
            if len(row) > col and row[col] in wanted:
                if header is None:
                    header = head
                if head == header:
                    rows.append(row)
    return header, rows


def build_zip(keys, folder, read_object, manifest_texts):
    """Write the album zip to a temp file and return its path.

    Photos keep their original bytes (stored, not recompressed: JPEGs do not
    shrink and the EXIF must survive). Numbered in the order given, which is
    the order they were taken."""
    names = [k.rsplit('/', 1)[-1] for k in keys]
    fd, path = tempfile.mkstemp(suffix='.zip')
    os.close(fd)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        width = max(3, len(str(len(keys))))
        for i, (key, name) in enumerate(zip(keys, names), 1):
            z.writestr(f'{folder}/{i:0{width}d}_{name}', read_object(key))
        header, rows = manifest_rows(manifest_texts, names)
        if header:
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator='\n')
            w.writerow(header)
            w.writerows(rows)
            z.writestr(f'{folder}/manifest.csv', buf.getvalue(),
                       compress_type=zipfile.ZIP_DEFLATED)
    return path


def _table():
    return C.dynamodb.Table(C.CONVERSATIONS_TABLE)


def _share_base(event):
    """Where /share/<id> is served. API Gateway says its own domain and stage;
    the ground station sets SHARE_BASE_URL."""
    base = os.environ.get('SHARE_BASE_URL')
    if base:
        return base.rstrip('/')
    rc = event.get('requestContext') or {}
    domain = rc.get('domainName') or (event.get('headers') or {}).get('Host', '')
    stage = rc.get('stage') or ''
    return f"https://{domain}" + (f"/{stage}" if stage and stage != '$default' else '')


def _read_object(key):
    return C.s3.get_object(Bucket=C.IMAGES_BUCKET, Key=key)['Body'].read()


def _manifests_beside(keys):
    """Text of every survey manifest in the folders these photos are in."""
    texts = []
    for prefix in sorted({k.rsplit('/', 1)[0] + '/' for k in keys}):
        token = None
        while True:
            kw = {'Bucket': C.IMAGES_BUCKET, 'Prefix': prefix}
            if token:
                kw['ContinuationToken'] = token
            resp = C.s3.list_objects_v2(**kw)
            for obj in resp.get('Contents', []):
                k = obj['Key']
                name = k.rsplit('/', 1)[-1]
                if k.rsplit('/', 1)[0] + '/' == prefix and 'manifest' in name and name.endswith('.csv'):
                    try:
                        texts.append(_read_object(k).decode('utf-8'))
                    except Exception as e:
                        print(f"album: manifest {k} unreadable: {e}")
            if not resp.get('IsTruncated'):
                break
            token = resp.get('NextContinuationToken')
    return texts


def album_handler(event, context):
    """POST /albums {"photos": [url, ...]} -> {"url": share link, ...}."""
    user_id = C.get_user_id(event)
    if not user_id:
        return C.json_response(401, {'error': 'Unauthorized'})
    try:
        body = json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        return C.json_response(400, {'error': 'Invalid JSON'})

    urls = body.get('photos') or []
    if not isinstance(urls, list) or not urls:
        return C.json_response(400, {'error': 'photos: a list of photo URLs is required'})
    if len(urls) > MAX_PHOTOS:
        return C.json_response(400, {'error': f'at most {MAX_PHOTOS} photos per album'})
    keys, seen = [], set()
    for u in urls:
        k = photo_key(u)
        if not k or not k.lower().endswith(PHOTO_EXTENSIONS):
            return C.json_response(400, {'error': f'not a photo URL: {str(u)[:120]}'})
        if k not in seen:
            seen.add(k)
            keys.append(k)

    drone_id = album_drone_id(keys)
    if not drone_id:
        return C.json_response(400, {'error': "photos must all be one drone's"})
    if not C.verify_ownership(user_id, drone_id):
        return C.json_response(403, {'error': 'You do not own this drone'})

    aid = album_id(keys)
    table = _table()
    now = int(time.time())
    existing = table.get_item(Key={'PK': f'ALBUM#{aid}', 'SK': 'ALBUM'}).get('Item')
    if existing and int(existing.get('ttl', 0)) > now + 86400:
        return C.json_response(200, {
            'url': f"{_share_base(event)}/share/{existing['token']}",
            'photos': int(existing.get('photos', len(keys))),
            'expiresAt': _iso(int(existing['ttl'])),
        })

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
    folder = f'coybot-survey-{stamp}'
    zip_key = f"{keys[0].rsplit('/', 1)[0]}/albums/{aid}.zip"
    try:
        path = build_zip(keys, folder, _read_object, _manifests_beside(keys))
    except Exception as e:
        # Most likely a photo still on its way up from the drone.
        print(f"album {aid}: build failed: {e}")
        return C.json_response(409, {'error': 'Some photos are not uploaded yet. Try again shortly.'})
    try:
        with open(path, 'rb') as f:
            C.s3.put_object(Bucket=C.IMAGES_BUCKET, Key=zip_key, Body=f,
                            ContentType='application/zip')
        size = os.path.getsize(path)
    finally:
        os.unlink(path)

    token = secrets.token_urlsafe(16)
    ttl = now + SHARE_DAYS * 86400
    # No droneId attribute: these must stay out of the droneId-index that
    # lists a drone's conversations.
    table.put_item(Item={'PK': f'SHARE#{token}', 'SK': 'SHARE', 'zipKey': zip_key,
                         'filename': f'{folder}.zip', 'ownerId': user_id,
                         'photos': len(keys), 'createdAt': now, 'ttl': ttl})
    table.put_item(Item={'PK': f'ALBUM#{aid}', 'SK': 'ALBUM', 'token': token,
                         'zipKey': zip_key, 'photos': len(keys), 'createdAt': now, 'ttl': ttl})
    print(f"album {aid}: {len(keys)} photos, {size} bytes -> {zip_key}")
    return C.json_response(200, {
        'url': f"{_share_base(event)}/share/{token}",
        'photos': len(keys),
        'expiresAt': _iso(ttl),
    })


def share_handler(event, context):
    """GET /share/{token} - public. Redirects to a fresh download of the zip."""
    token = ((event.get('pathParameters') or {}).get('token') or '').strip()
    item = None
    if token and len(token) <= 64:
        item = _table().get_item(Key={'PK': f'SHARE#{token}', 'SK': 'SHARE'}).get('Item')
    if not item or int(item.get('ttl', 0)) < time.time():
        return {'statusCode': 404, 'headers': {'Content-Type': 'text/plain; charset=utf-8'},
                'body': 'This link has expired or does not exist.\n'}
    filename = item.get('filename') or 'coybot-survey.zip'
    url = C.s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': C.IMAGES_BUCKET, 'Key': item['zipKey'],
                'ResponseContentDisposition': f'attachment; filename="{filename}"',
                'ResponseContentType': 'application/zip'},
        ExpiresIn=DOWNLOAD_EXPIRES_S)
    return {'statusCode': 302,
            'headers': {'Location': url, 'Cache-Control': 'no-store',
                        'Content-Type': 'text/plain; charset=utf-8'},
            'body': f'Download: {url}\n'}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
