"""
Focused tests for http_api.py's /images/{key} route - the two valid upload
auths (a short-lived HMAC-signed URL for the app's own presigned-upload flow,
or a drone's own long-lived pairing token for drone_sdk.py's/sim_control.py's
direct photo uploads) and GET for viewing. Doesn't need the full
subprocess+mosquitto stack in test_local_stack.py since this route has no
aws/src dependency at all.

Run: cd eco/gcs && python3 -m pytest tests/test_http_api_images.py -v
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import AuthStore  # noqa: E402
from http_api import GCSHttpServer  # noqa: E402
from signing import ImageUrlSigner  # noqa: E402


@pytest.fixture
def server(tmp_path):
    auth_store = AuthStore(tmp_path / "auth.json", operator_names=["op1"])
    drone_token = auth_store.ensure_drone_token("sim-q1")
    signer = ImageUrlSigner("test-secret")
    images_dir = tmp_path / "images"

    srv = GCSHttpServer(
        host="127.0.0.1", port=0, routes=[], auth_store=auth_store,
        images_dir=images_dir, sign_verify_fn=signer.verify,
    )
    srv.start()
    yield {"base_url": srv.base_url, "signer": signer, "drone_token": drone_token, "images_dir": images_dir}
    srv.stop()


def _put(url, data, headers=None):
    req = urllib.request.Request(url, data=data, method="PUT")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, None


def test_put_with_valid_signed_token_succeeds(server):
    key = "drones/sim-q1/conversations/c1/photo.jpg"
    token = server["signer"].sign(key, expires_in=300)
    status = _put(f"{server['base_url']}/images/{key}?token={token}", b"jpegbytes")
    assert status == 200
    assert (server["images_dir"] / key).read_bytes() == b"jpegbytes"


def test_put_with_valid_drone_token_succeeds(server):
    key = "drones/sim-q1/conversations/c1/photo2.jpg"
    status = _put(
        f"{server['base_url']}/images/{key}", b"morebytes",
        headers={"Authorization": f"Bearer {server['drone_token']}"},
    )
    assert status == 200
    assert (server["images_dir"] / key).read_bytes() == b"morebytes"


def test_put_with_no_credentials_rejected(server):
    key = "drones/sim-q1/conversations/c1/photo3.jpg"
    status = _put(f"{server['base_url']}/images/{key}", b"nope")
    assert status == 403


def test_put_with_wrong_drone_token_rejected(server):
    key = "drones/sim-q1/conversations/c1/photo4.jpg"
    status = _put(
        f"{server['base_url']}/images/{key}", b"nope",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert status == 403


def test_put_with_expired_signed_token_rejected(server):
    key = "drones/sim-q1/conversations/c1/photo5.jpg"
    token = server["signer"].sign(key, expires_in=-10)  # already expired
    status = _put(f"{server['base_url']}/images/{key}?token={token}", b"nope")
    assert status == 403


def test_get_after_put_returns_bytes(server):
    key = "drones/sim-q1/conversations/c1/photo6.jpg"
    _put(f"{server['base_url']}/images/{key}",
         b"viewme", headers={"Authorization": f"Bearer {server['drone_token']}"})
    status, body = _get(f"{server['base_url']}/images/{key}")
    assert status == 200
    assert body == b"viewme"


def test_get_missing_key_404(server):
    status, _ = _get(f"{server['base_url']}/images/drones/sim-q1/conversations/c1/nope.jpg")
    assert status == 404


def test_path_traversal_rejected(server):
    status = _put(f"{server['base_url']}/images/../../etc/passwd", b"x")
    assert status == 400
