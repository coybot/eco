"""Presigned S3 URLs must use the bucket's regional hostname (see clients.regional_s3_client)."""

def test_presigned_urls_use_regional_s3_hostname(monkeypatch):
    import clients
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    url = clients.regional_s3_client().generate_presigned_url(
        "get_object", Params={"Bucket": "coybot-drone-images-dev-us-east-2", "Key": "drones/x.jpg"},
        ExpiresIn=60)
    assert url.startswith("https://coybot-drone-images-dev-us-east-2.s3.us-east-2.amazonaws.com/drones/x.jpg?")
