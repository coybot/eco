"""
HMAC-signed, time-limited tokens for GCS-local image URLs - stands in for S3
presigned-URL signatures (LocalS3.generate_presigned_url in local_aws.py signs,
http_api.py's PUT /images/{key} route verifies). One secret, generated once and
stored alongside the pairing tokens in auth.json.
"""
from __future__ import annotations

import hashlib
import hmac
import time


class ImageUrlSigner:
    def __init__(self, secret: str):
        self._secret = secret.encode("utf-8")

    def sign(self, key: str, expires_in: int) -> str:
        """Returns a single opaque token string embedding its own expiry -
        callers don't need to track/pass expires_at separately."""
        exp = int(time.time()) + expires_in
        return f"{self._mac(key, exp)}.{exp}"

    def verify(self, key: str, token: str) -> bool:
        if not token or "." not in token:
            return False
        digest, _, exp_str = token.rpartition(".")
        try:
            exp = int(exp_str)
        except ValueError:
            return False
        if time.time() > exp:
            return False
        return hmac.compare_digest(digest, self._mac(key, exp))

    def _mac(self, key: str, exp: int) -> str:
        return hmac.new(self._secret, f"{key}:{exp}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]
