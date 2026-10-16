"""A mission's album has to leave the app as one web link anyone can open.

Pins:
1. The link is stable (same album -> same link, no rebuild) and is a /share/<id>
   on the API's own address, not a presigned URL that dies with the Lambda's
   credentials.
2. The zip holds the original photo bytes in the order taken, plus only the
   manifest rows for those photos (a conversation can hold several surveys).
3. Only the drone's owner can make one, and the drone is read from the photos'
   own keys - naming another drone in the request is not possible.
4. Opening the link needs no sign-in and shows a page of the photos (where each
   was taken, Download all); an unknown or expired link says so.

Run: cd eco/aws/src && python3 -m pytest test_albums.py -v
"""
import io
import json
import time
import zipfile
from unittest.mock import patch

import albums
import conversations as C

D = "drones/d1/conversations/c1"
BUCKET = "https://drone-images-dev.s3.amazonaws.com"


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.reads = 0

    def get_object(self, Bucket, Key):
        self.reads += 1
        if Key not in self.objects:
            raise Exception(f"NoSuchKey {Key}")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = Body.read() if hasattr(Body, "read") else Body

    def list_objects_v2(self, Bucket, Prefix, **_):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)],
                "IsTruncated": False}

    def generate_presigned_url(self, method, Params, ExpiresIn):
        return f"{BUCKET}/{Params['Key']}?X-Amz-Signature=x&disp={Params.get('ResponseContentDisposition', '')}"


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)


class FakeDynamo:
    def __init__(self):
        self.table = FakeTable()

    def Table(self, name):
        return self.table


MANIFEST = (
    "n,file,url,utc_time,lat,lon\n"
    "1,p1.jpg,u,2026-10-16T04:44:33Z,-35.1,149.1\n"
    "2,p2.jpg,u,2026-10-16T04:44:35Z,-35.2,149.2\n"
    "3,other.jpg,u,2026-10-16T04:50:00Z,-35.3,149.3\n"
)


def _store():
    return FakeS3({
        f"{D}/p1.jpg": b"\xff\xd8one",
        f"{D}/p2.jpg": b"\xff\xd8two",
        f"{D}/other.jpg": b"\xff\xd8other",
        f"{D}/20261015_survey_manifest_ab.csv": MANIFEST.encode(),
    })


def _event(photos, user="u1"):
    return {"body": json.dumps({"photos": photos}),
            "requestContext": {"authorizer": {"userId": user},
                               "domainName": "abc.execute-api.us-west-2.amazonaws.com",
                               "stage": "prod"}}


def _call(s3, dyn, photos, user="u1", owns=lambda u, d: (u, d) == ("u1", "d1")):
    with patch.object(C, "s3", s3), patch.object(C, "dynamodb", dyn), \
         patch.object(C, "verify_ownership", owns), \
         patch.dict("os.environ", {}, clear=False) as env:
        env.pop("SHARE_BASE_URL", None)
        r = albums.album_handler(_event(photos, user), None)
    return r["statusCode"], json.loads(r["body"])


def _share(s3, dyn, token):
    with patch.object(C, "s3", s3), patch.object(C, "dynamodb", dyn):
        return albums.share_handler({"pathParameters": {"token": token}}, None)


def _signed(name):
    return f"{BUCKET}/{D}/{name}?X-Amz-Algorithm=AWS4&X-Amz-Signature=abc"


def test_a_link_is_a_stable_share_url_on_the_api():
    s3, dyn = _store(), FakeDynamo()
    code, body = _call(s3, dyn, [_signed("p1.jpg"), _signed("p2.jpg")])
    assert code == 200
    assert body["url"].startswith("https://abc.execute-api.us-west-2.amazonaws.com/prod/share/")
    assert body["photos"] == 2
    reads = s3.reads
    # Asked again, in another order (another device): same link, nothing rebuilt.
    code2, body2 = _call(s3, dyn, [_signed("p2.jpg"), _signed("p1.jpg")])
    assert (code2, body2["url"]) == (200, body["url"])
    assert s3.reads == reads


def test_the_zip_has_the_original_photos_in_order_and_only_their_manifest_rows():
    s3, dyn = _store(), FakeDynamo()
    _, body = _call(s3, dyn, [_signed("p2.jpg"), _signed("p1.jpg")])
    zips = [k for k in s3.objects if k.endswith(".zip")]
    assert len(zips) == 1 and zips[0].startswith(f"{D}/albums/")
    z = zipfile.ZipFile(io.BytesIO(s3.objects[zips[0]]))
    names = z.namelist()
    photos = [n for n in names if n.endswith(".jpg")]
    assert [n.split("/", 1)[1] for n in photos] == ["001_p2.jpg", "002_p1.jpg"]
    assert z.read(photos[0]) == b"\xff\xd8two"          # untouched bytes
    manifest = z.read([n for n in names if n.endswith("manifest.csv")][0]).decode()
    assert "p1.jpg" in manifest and "p2.jpg" in manifest
    assert "other.jpg" not in manifest                    # another survey's row


def _share_q(s3, dyn, token, query=None):
    with patch.object(C, "s3", s3), patch.object(C, "dynamodb", dyn):
        return albums.share_handler({"pathParameters": {"token": token},
                                     "queryStringParameters": query}, None)


def test_the_link_opens_a_page_of_the_photos_with_no_sign_in():
    s3, dyn = _store(), FakeDynamo()
    _, body = _call(s3, dyn, [_signed("p1.jpg"), _signed("p2.jpg")])
    token = body["url"].rsplit("/", 1)[1]
    r = _share(s3, dyn, token)
    assert r["statusCode"] == 200
    assert r["headers"]["Content-Type"].startswith("text/html")
    page = r["body"]
    assert "2 photos" in page
    # each photo shown from a fresh signed URL, in the order taken
    assert page.index(f"{D}/p1.jpg?X-Amz-Signature") < page.index(f"{D}/p2.jpg?X-Amz-Signature")
    assert 'href="?download=1"' in page
    # positions from the drone's manifest: per photo, and the plot
    assert "maps.google.com/?q=-35.1000000,149.1000000" in page and "<svg" in page
    assert "04:44:33 UTC" in page
    assert "<script" not in page


def test_download_all_redirects_to_the_zip():
    s3, dyn = _store(), FakeDynamo()
    _, body = _call(s3, dyn, [_signed("p1.jpg"), _signed("p2.jpg")])
    token = body["url"].rsplit("/", 1)[1]
    r = _share_q(s3, dyn, token, {"download": "1"})
    assert r["statusCode"] == 302
    loc = r["headers"]["Location"]
    assert f"{D}/albums/" in loc and loc.split("?")[0].endswith(".zip")
    assert "attachment" in loc


def test_a_drone_chosen_file_name_cannot_inject_into_the_page():
    s3, dyn = _store(), FakeDynamo()
    evil = 'x"><script>alert(1)</script>.jpg'
    s3.objects[f"{D}/{evil}"] = b"\xff\xd8"
    _, body = _call(s3, dyn, [_signed("p1.jpg"), f"{BUCKET}/{D}/{evil}"])
    page = _share(s3, dyn, body["url"].rsplit("/", 1)[1])["body"]
    assert "<script>" not in page


def test_an_album_without_positions_still_has_its_page():
    s3, dyn = _store(), FakeDynamo()
    del s3.objects[f"{D}/20261015_survey_manifest_ab.csv"]
    _, body = _call(s3, dyn, [_signed("p1.jpg"), _signed("p2.jpg")])
    page = _share(s3, dyn, body["url"].rsplit("/", 1)[1])["body"]
    assert "p2.jpg" in page and "<svg" not in page


def test_unknown_or_expired_links_say_so():
    s3, dyn = _store(), FakeDynamo()
    r = _share(s3, dyn, "nope")
    assert r["statusCode"] == 404 and "expired" in r["body"]
    _, body = _call(s3, dyn, [_signed("p1.jpg"), _signed("p2.jpg")])
    token = body["url"].rsplit("/", 1)[1]
    dyn.table.items[(f"SHARE#{token}", "SHARE")]["ttl"] = int(time.time()) - 1
    r = _share(s3, dyn, token)
    assert r["statusCode"] == 404 and "expired" in r["body"]


def test_only_the_drones_owner_can_make_a_link():
    s3, dyn = _store(), FakeDynamo()
    code, _ = _call(s3, dyn, [_signed("p1.jpg")], user="someone-else")
    assert code == 403
    assert not any(k.endswith(".zip") for k in s3.objects)


def test_photos_from_two_drones_or_outside_drones_are_refused():
    s3, dyn = _store(), FakeDynamo()
    s3.objects["drones/d2/conversations/c9/x.jpg"] = b"x"
    code, _ = _call(s3, dyn, [_signed("p1.jpg"), f"{BUCKET}/drones/d2/conversations/c9/x.jpg"])
    assert code == 400
    code, _ = _call(s3, dyn, [f"{BUCKET}/private/secret.jpg"])
    assert code == 400
    code, _ = _call(s3, dyn, [f"{BUCKET}/drones/d1/../d2/x.jpg"])
    assert code == 400


def test_a_photo_still_uploading_is_a_retry_not_a_broken_zip():
    s3, dyn = _store(), FakeDynamo()
    code, body = _call(s3, dyn, [_signed("p1.jpg"), _signed("not-yet.jpg")])
    assert code == 409 and "not uploaded yet" in body["error"]
    assert not any(k.endswith(".zip") for k in s3.objects)
    assert not dyn.table.items


def test_share_records_stay_out_of_the_drones_conversation_index():
    s3, dyn = _store(), FakeDynamo()
    _call(s3, dyn, [_signed("p1.jpg"), _signed("p2.jpg")])
    assert dyn.table.items and all("droneId" not in i for i in dyn.table.items.values())


def test_photo_key_reads_every_url_form_the_app_holds():
    k = f"{D}/p1.jpg"
    assert albums.photo_key(f"{BUCKET}/{k}") == k
    assert albums.photo_key(f"https://b.s3.us-west-2.amazonaws.com/{k}?X-Amz-Signature=1") == k
    assert albums.photo_key(f"https://s3.us-west-2.amazonaws.com/b/{k}") == k
    assert albums.photo_key(f"http://127.0.0.1:18080/images/{k}?token=t") == k
    assert albums.photo_key(f"{BUCKET}/drones/d1/conversations/c1/a%20b.jpg") == f"{D}/a b.jpg"
    assert albums.photo_key("file:///etc/passwd") is None
    assert albums.photo_key(f"{BUCKET}/drones/../etc.jpg") is None
