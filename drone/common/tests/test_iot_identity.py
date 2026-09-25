"""Photos have to reach S3 under the drone's real identity.

With the camera fixed, the quadcopter's first ground-test photo was captured
and then stranded: drone_sdk asked config.yaml for drone_id, found none (the
daemon reads it from /etc/drone-id), printed "Missing drone_id in config",
got no AWS credentials, and capture_photo fell back to returning a local
path. Had it found the drone ID, it would still have failed: the device
certificate is attached to IoT thing "aircraft-thing-01", and the credential
provider answers the drone ID with "403 Invalid thing name". Both were
measured on the aircraft.

Run: cd eco && python3 -m pytest drone/common/tests/test_iot_identity.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk as sdk  # noqa: E402
import provisioning  # noqa: E402


@pytest.fixture
def aircraft(tmp_path, monkeypatch):
    """The quadcopter's layout: ID in /etc/drone-id, thing name in config."""
    etc = tmp_path / "drone-id"
    etc.write_text("drone-0123456789ab\n")
    monkeypatch.setattr(provisioning, "DRONE_ID_FILE", str(etc))
    monkeypatch.setattr(provisioning, "DRONE_ID_FALLBACK_FILE", tmp_path / "fallback")
    certs = tmp_path / "certs"
    certs.mkdir()
    for name in ("device.pem", "private.key", "root-ca.pem"):
        (certs / name).write_text("x")
    monkeypatch.setattr(sdk, "DRONE_DIR", tmp_path)
    config = {"iot_thing_name": "aircraft-thing-01",
              "credentials_endpoint": "creds.example", "region": "us-west-2"}
    monkeypatch.setattr(sdk, "_load_config", lambda: dict(config))
    return config, tmp_path


class Response:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text

    def json(self):
        return {"credentials": {"accessKeyId": "A", "secretAccessKey": "S", "sessionToken": "T",
                                "expiration": "2026-09-25T20:00:00Z"}}


def _stub_credential_provider(monkeypatch, accepts):
    """Answer like AWS: 200 for a header in `accepts`, else 403. Installed as
    the `requests` module, which _get_iot_credentials imports on each call."""
    import types
    seen = []

    def fake_get(url, cert, verify, headers, timeout=None):
        name = headers.get("x-amzn-iot-thingname")
        seen.append(name)
        return Response(200) if name in accepts else Response(403, '{"message":"Invalid thing name passed"}')

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    return seen


def test_identity_matches_the_daemon_and_the_certificate(aircraft):
    assert sdk._drone_identity() == ("drone-0123456789ab", "aircraft-thing-01")


def test_credentials_are_requested_for_the_certificates_thing(aircraft, monkeypatch):
    # The config file itself is read inside _get_iot_credentials.
    config, root = aircraft
    import yaml
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    seen = _stub_credential_provider(monkeypatch, accepts={"aircraft-thing-01"})
    creds = sdk._get_iot_credentials()
    assert creds and creds["access_key"] == "A"
    assert seen == ["aircraft-thing-01"]


def test_a_refused_thing_name_is_retried_without_the_header(aircraft, monkeypatch):
    config, root = aircraft
    config.pop("iot_thing_name")   # falls back to the drone ID, which AWS refuses
    import yaml
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    seen = _stub_credential_provider(monkeypatch, accepts={None})
    assert sdk._get_iot_credentials() is not None
    assert seen == ["drone-0123456789ab", None]


def test_photo_keys_carry_the_real_drone_id(aircraft):
    key = sdk._media_key("conv-1", "photo.jpg")
    assert key.startswith("drones/drone-0123456789ab/conversations/conv-1/")
    assert "unknown" not in key
