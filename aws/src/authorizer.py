"""
Lambda Authorizer for validating Google/Apple/Cognito tokens
"""
import json
import os
import urllib.request
import base64
import hashlib
import hmac
import time
from functools import lru_cache

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
USER_POOL_ID = os.environ.get('USER_POOL_ID', '')
USER_POOL_REGION = (
    os.environ.get("USER_POOL_REGION")
    or os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "us-west-2"
)

# JWKS cache TTL in seconds
JWKS_CACHE_TTL = 3600

# In-memory JWKS cache
_jwks_cache = {}


@lru_cache(maxsize=4)
def fetch_jwks(jwks_url: str) -> dict:
    """Fetch JWKS from URL with caching."""
    try:
        req = urllib.request.Request(jwks_url)
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Failed to fetch JWKS from {jwks_url}: {e}")
        return {'keys': []}


def get_cognito_jwks_url() -> str:
    """Get Cognito User Pool JWKS URL."""
    return f"https://cognito-idp.{USER_POOL_REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"


def get_apple_jwks_url() -> str:
    """Get Apple's JWKS URL."""
    return "https://appleid.apple.com/auth/keys"


def base64url_decode(data: str) -> bytes:
    """Decode base64url encoded data."""
    # Add padding if needed
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.urlsafe_b64decode(data)


def int_from_bytes(b: bytes) -> int:
    """Convert bytes to integer (big-endian)."""
    return int.from_bytes(b, byteorder='big')


def verify_rs256_signature(token: str, jwks: dict) -> bool:
    """
    Verify RS256 JWT signature using JWKS.

    This is a minimal RS256 verification without external crypto libraries.
    Uses Python's built-in pow() for RSA verification.
    """
    import secrets
    import traceback as _tb
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return False

        header_b64, payload_b64, signature_b64 = parts

        # Decode header to get kid
        header = json.loads(base64url_decode(header_b64))
        kid = header.get('kid')
        alg = header.get('alg')

        if alg != 'RS256':
            print(f"Unsupported algorithm: {alg}")
            return False

        # Find matching key in JWKS
        key = None
        for k in jwks.get('keys', []):
            if k.get('kid') == kid and k.get('kty') == 'RSA':
                key = k
                break

        if not key:
            print(f"No matching key found for kid: {kid}")
            return False

        # Extract RSA public key components
        n_bytes = base64url_decode(key['n'])
        e_bytes = base64url_decode(key['e'])
        n = int_from_bytes(n_bytes)
        e = int_from_bytes(e_bytes)

        # Decode signature
        sig_bytes = base64url_decode(signature_b64)
        signature = int_from_bytes(sig_bytes)

        # RSA verification: signature^e mod n
        decrypted = pow(signature, e, n)

        # Use actual key size in bytes (avoids hardcoded 256 for non-2048-bit keys)
        key_size = len(n_bytes)
        decrypted_bytes = decrypted.to_bytes(key_size, byteorder='big')

        # PKCS#1 v1.5 padding check
        # Format: 0x00 0x01 [padding 0xFF bytes] 0x00 [DigestInfo + Hash]
        if decrypted_bytes[0:2] != b'\x00\x01':
            return False

        # Find the 0x00 separator
        separator_idx = decrypted_bytes.find(b'\x00', 2)
        if separator_idx == -1:
            return False

        # SHA-256 DigestInfo prefix for PKCS#1 v1.5
        sha256_digest_info = bytes([
            0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86,
            0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01, 0x05,
            0x00, 0x04, 0x20
        ])

        digest_info_and_hash = decrypted_bytes[separator_idx + 1:]

        if not digest_info_and_hash.startswith(sha256_digest_info):
            return False

        extracted_hash = bytes(digest_info_and_hash[len(sha256_digest_info):])

        # Compute expected hash over the JWT signing input (header.payload as ASCII bytes)
        message = (header_b64 + '.' + payload_b64).encode('ascii')
        expected_hash = bytes(hashlib.sha256(message).digest())

        if len(extracted_hash) != len(expected_hash):
            return False

        return secrets.compare_digest(extracted_hash, expected_hash)

    except Exception as e:
        print(f"Signature verification error: {e}")
        print(_tb.format_exc())
        return False


def handler(event, context):
    """
    Validate the token from the Authorization header.
    Supports Google ID tokens, Apple ID tokens, and Cognito tokens.
    """
    try:
        token = extract_token(event)
        if not token:
            return deny_policy(event['methodArn'])
        
        # Try to validate as Google token
        user_info = validate_google_token(token)
        if user_info:
            return allow_policy(event['methodArn'], user_info['sub'], user_info)
        
        # Try to validate as Cognito token
        user_info = validate_cognito_token(token)
        if user_info:
            return allow_policy(event['methodArn'], user_info['sub'], user_info)
        
        # Try to validate as Apple token
        user_info = validate_apple_token(token)
        if user_info:
            return allow_policy(event['methodArn'], user_info['sub'], user_info)
        
        print("Token validation failed for all providers")
        return deny_policy(event['methodArn'])
        
    except Exception as e:
        print(f"Authorization error: {e}")
        return deny_policy(event['methodArn'])


def extract_token(event):
    """Extract Bearer token from Authorization header"""
    auth_header = event.get('authorizationToken', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    return auth_header


def decode_jwt_payload(token):
    """Decode JWT payload without verification (verification done by issuer check)"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        
        # Decode payload (middle part)
        payload = parts[1]
        # Add padding if needed
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        print(f"JWT decode error: {e}")
        return None


def validate_google_token(token):
    """Validate Google ID token"""
    if not GOOGLE_CLIENT_ID:
        return None
    
    try:
        # Use Google's tokeninfo endpoint
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
        req = urllib.request.Request(url)
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        # Verify audience matches our client ID
        if data.get('aud') != GOOGLE_CLIENT_ID:
            print(f"Google token audience mismatch: {data.get('aud')}")
            return None
        
        # Verify issuer
        if data.get('iss') not in ['accounts.google.com', 'https://accounts.google.com']:
            print(f"Google token issuer mismatch: {data.get('iss')}")
            return None
        
        return {
            'sub': f"google_{data.get('sub', '')}",
            'email': data.get('email'),
            'name': data.get('name'),
            'provider': 'google'
        }
    except urllib.error.HTTPError as e:
        print(f"Google token validation failed: {e.code}")
        return None
    except Exception as e:
        print(f"Google token validation error: {e}")
        return None


def validate_cognito_token(token):
    """Validate Cognito User Pool token with signature verification"""
    if not USER_POOL_ID:
        return None
    
    try:
        payload = decode_jwt_payload(token)
        if not payload:
            return None
        
        # Check issuer matches our User Pool
        expected_issuer = f"https://cognito-idp.{USER_POOL_REGION}.amazonaws.com/{USER_POOL_ID}"
        if payload.get('iss') != expected_issuer:
            return None
        
        # Check token expiration
        exp = payload.get('exp')
        if exp and int(time.time()) > exp:
            print("Cognito token expired")
            return None
        
        # Verify signature using Cognito JWKS
        jwks = fetch_jwks(get_cognito_jwks_url())
        if not verify_rs256_signature(token, jwks):
            print("Cognito token signature verification failed")
            return None
        
        return {
            'sub': payload.get('sub', ''),
            'email': payload.get('email'),
            'name': payload.get('name'),
            'provider': 'cognito'
        }
    except Exception as e:
        print(f"Cognito token validation error: {e}")
        return None


def validate_apple_token(token):
    """Validate Apple ID token with signature verification"""
    try:
        payload = decode_jwt_payload(token)
        if not payload:
            return None
        
        # Check issuer
        if payload.get('iss') != 'https://appleid.apple.com':
            return None
        
        # Check token expiration
        exp = payload.get('exp')
        if exp and int(time.time()) > exp:
            print("Apple token expired")
            return None
        
        # Verify signature using Apple JWKS
        jwks = fetch_jwks(get_apple_jwks_url())
        if not verify_rs256_signature(token, jwks):
            print("Apple token signature verification failed")
            return None
        
        return {
            'sub': f"apple_{payload.get('sub', '')}",
            'email': payload.get('email'),
            'provider': 'apple'
        }
    except Exception as e:
        print(f"Apple token validation error: {e}")
        return None


def allow_policy(method_arn, principal_id, context):
    """Generate allow policy"""
    # Extract the API ARN without method and path
    arn_parts = method_arn.split(':')
    api_gateway_arn = ':'.join(arn_parts[:5])
    rest_api_arn = arn_parts[5].split('/')[0]
    stage = arn_parts[5].split('/')[1]
    
    return {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': 'Allow',
                'Resource': f"{api_gateway_arn}:{rest_api_arn}/{stage}/*"
            }]
        },
        'context': {
            'userId': principal_id,
            'email': context.get('email', ''),
            'provider': context.get('provider', '')
        }
    }


def deny_policy(method_arn):
    """Generate deny policy"""
    return {
        'principalId': 'unauthorized',
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': 'Deny',
                'Resource': method_arn
            }]
        }
    }

