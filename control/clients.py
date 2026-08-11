"""
Storage / pub-sub client seam, shared by conversations.py, handler.py, drones.py,
and groups.py.

Backend selection is explicit and NOT import-order-sensitive. get_dynamodb()/
get_iot()/get_s3() each return a stable proxy object immediately; the concrete
client a proxy forwards to is resolved lazily, on first real attribute access
(e.g. the .Table(...) call, not the `dynamodb = clients.get_dynamodb()` import-
time binding) - so select_backend() can be called before OR after
handler.py/conversations.py/drones.py/groups.py are imported, as long as it
runs before the first real request is served.

Default backend (no select_backend() call) is AwsBackend - real, lazily
constructed boto3 clients, identical to the deployed Lambda stack's behavior
with zero configuration.

GCS (local ground control station) mode: gcs/server.py builds a backend whose
.dynamodb/.iot/.s3 are the local stand-ins (SQLite-backed DynamoDB, a
paho-mqtt-backed IoT publisher, a filesystem-backed S3 - see
control/backends/local.py) and calls select_backend(that_backend).

Not covered by this seam (cloud-only, untouched): drones.py's control-plane
`iot` client (`boto3.client('iot')`, used for Cognito IoT-policy attach/detach)
and video.py's Kinesis Video Streams client - neither has a GCS equivalent.
"""
from __future__ import annotations

# Dual import: packaged (control.backends.aws) when `control` is a real
# package on sys.path, flat (backends.aws) in a Lambda zip whose root IS this
# directory's contents (CodeUri points at control/, so `backends/` sits
# alongside this file with no enclosing `control` package).
try:
    from .backends.aws import AwsBackend  # type: ignore
except ImportError:  # pragma: no cover - flat Lambda zip install
    from backends.aws import AwsBackend  # type: ignore

_backend = None


def _active_backend():
    global _backend
    if _backend is None:
        _backend = AwsBackend()
    return _backend


class _LazyClient:
    """Forwards every attribute access to the active backend's client,
    resolved at access time rather than at construction time."""

    def __init__(self, attr: str):
        self._attr = attr

    def __getattr__(self, name):
        target = getattr(_active_backend(), self._attr)
        return getattr(target, name)


_dynamodb_proxy = _LazyClient("dynamodb")
_iot_proxy = _LazyClient("iot")
_s3_proxy = _LazyClient("s3")


def select_backend(backend) -> None:
    """Make `backend` (an object exposing .dynamodb, .iot, .s3, and
    .image_url_fn(key)) the active backend for every get_dynamodb()/
    get_iot()/get_s3()/public_image_url() call from here on. See AwsBackend
    and control.backends.local.LocalBackend."""
    global _backend
    _backend = backend


class _AdHocBackend:
    """Backs configure() below: lets a caller (e.g. a test) inject individual
    stand-ins without building a full backend object."""

    def __init__(self):
        self.dynamodb = None
        self.iot = None
        self.s3 = None
        self._image_url_fn = None

    def image_url_fn(self, key: str) -> str:
        if self._image_url_fn is not None:
            return self._image_url_fn(key)
        return AwsBackend().image_url_fn(key)


def configure(dynamodb=None, iot=None, s3=None, image_url_fn=None) -> None:
    """Back-compat / test-injection helper: set individual stand-ins on an
    ad hoc backend. Prefer select_backend() for a fully-formed backend."""
    global _backend
    if not isinstance(_backend, _AdHocBackend):
        _backend = _AdHocBackend()
    if dynamodb is not None:
        _backend.dynamodb = dynamodb
    if iot is not None:
        _backend.iot = iot
    if s3 is not None:
        _backend.s3 = s3
    if image_url_fn is not None:
        _backend._image_url_fn = image_url_fn


def get_dynamodb():
    return _dynamodb_proxy


def get_iot():
    return _iot_proxy


def get_s3():
    return _s3_proxy


def public_image_url(key: str) -> str:
    """Public (viewing) URL for an S3 object key. GCS mode's LocalBackend
    overrides this to point at its own local image server."""
    return _active_backend().image_url_fn(key)
