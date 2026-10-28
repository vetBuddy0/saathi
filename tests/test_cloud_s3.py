"""cloud/s3.py: SigV4 for one PUT. Pinned by AWS's own published example
(S3 docs, "Example: PUT Object"); while it was written it was also matched
against botocore on 200 random keys, bodies and regions."""

import datetime
import hashlib

from saathi.cloud import s3


def test_aws_published_put_example():
    body = b"Welcome to Amazon S3."
    digest = hashlib.sha256(body).hexdigest()
    assert digest == "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072"
    header = s3.authorization(
        method="PUT",
        host="examplebucket.s3.amazonaws.com",
        path="/test%24file.text",
        headers={
            "Date": "Fri, 24 May 2013 00:00:00 GMT",
            "x-amz-date": "20130524T000000Z",
            "x-amz-storage-class": "REDUCED_REDUNDANCY",
            "x-amz-content-sha256": digest,
        },
        payload_sha256=digest,
        when=datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc),
        region="us-east-1",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    assert header == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class, "
        "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"
    )


def test_the_request_goes_to_the_buckets_regional_host_with_the_key_encoded():
    target = s3.S3Target("AKIA1", "secret", "ap-southeast-1", "saathi-data")
    when = datetime.datetime(2026, 10, 29, 1, 2, 3, tzinfo=datetime.timezone.utc)
    request = s3.put_request(target, "devices/d1/transcripts/a b$.json", b"{}", "application/json", when)
    assert request.full_url == (
        "https://saathi-data.s3.ap-southeast-1.amazonaws.com/devices/d1/transcripts/a%20b%24.json"
    )
    assert request.get_method() == "PUT"
    assert request.headers["X-amz-date"] == "20261029T010203Z"
    assert request.headers["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=AKIA1/20261029/ap-southeast-1/s3/aws4_request, "
        "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, Signature="
    )


def test_all_four_names_or_no_target():
    full = {"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S", "AWS_REGION": "r", "AWS_S3_BUCKET": "b"}
    assert s3.S3Target.from_env(full) == s3.S3Target("A", "S", "r", "b")
    partial = dict(full, AWS_REGION=" ")
    assert s3.S3Target.from_env(partial) is None
    assert s3.missing(partial) == ["AWS_REGION"]
