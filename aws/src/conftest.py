"""aws/src tests never reach AWS.

The handler modules build their boto3 clients at import, and some tests drive a
whole handler with only part of it stubbed (e.g. response_handler's reply
record and mission record). With default AWS credentials on the machine, those
calls would write to the live tables. Remove every credential source and point
DynamoDB and IoT at a closed local port before any handler module is imported,
so a call a test forgot to stub fails at once instead. S3 keeps its real endpoint: tests
check the hostnames of presigned URLs, which are made offline.
"""
import os

# No credentials at all: an unstubbed call raises NoCredentialsError before
# any request is made (and without network retries).
for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
    os.environ.pop(var, None)
os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
os.environ["AWS_CONFIG_FILE"] = os.devnull
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ["AWS_ENDPOINT_URL_DYNAMODB"] = "http://127.0.0.1:9"
os.environ["AWS_ENDPOINT_URL_IOT_DATA_PLANE"] = "http://127.0.0.1:9"
