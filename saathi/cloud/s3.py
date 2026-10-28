"""One S3 PUT, signed with AWS Signature Version 4, on the standard library.

Why it exists: the owner asked for every conversation, the memory file
and her voice clips to be kept in AWS (2026-10-29), so a caregiver or
family portal can be built on them later. Uploading is all the device
does; reading, listing and sharing belong to that portal.

Lost: boto3/botocore. Correct by construction, but tens of megabytes on
a tablet APK that is already 62 MB, for one request type. The signer
here was checked against botocore's own SigV4 on random keys, bodies and
times while it was written, and is pinned by AWS's published example
(tests/test_cloud_s3.py).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

# Every name is AWS_ so the Android shell's key store passes the whole
# set through one prefix (saathi/android.py's KEY_PREFIXES).
ENV_NAMES = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_S3_BUCKET")

TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class S3Target:
    access_key: str
    secret_key: str
    region: str
    bucket: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> S3Target | None:
        values = [env.get(name, "").strip() for name in ENV_NAMES]
        return cls(*values) if all(values) else None

    @property
    def host(self) -> str:
        return f"{self.bucket}.s3.{self.region}.amazonaws.com"


def missing(env: Mapping[str, str]) -> list[str]:
    return [name for name in ENV_NAMES if not env.get(name, "").strip()]


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def authorization(
    *,
    method: str,
    host: str,
    path: str,
    headers: Mapping[str, str],
    payload_sha256: str,
    when: datetime.datetime,
    region: str,
    access_key: str,
    secret_key: str,
    service: str = "s3",
) -> str:
    """The `Authorization` header for a request with no query string.
    `path` is already URI-encoded; `headers` are the ones signed (host
    is added); names are compared lower-case."""
    signed = {name.lower(): " ".join(value.split()) for name, value in headers.items()}
    signed["host"] = host
    names = sorted(signed)
    canonical = "\n".join(
        [
            method,
            path,
            "",
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            payload_sha256,
        ]
    )
    day = when.strftime("%Y%m%d")
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            when.strftime("%Y%m%dT%H%M%SZ"),
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ]
    )
    key = _hmac(("AWS4" + secret_key).encode(), day)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )


def put_request(
    target: S3Target,
    key: str,
    body: bytes,
    content_type: str,
    when: datetime.datetime | None = None,
) -> urllib.request.Request:
    when = when or datetime.datetime.now(datetime.timezone.utc)
    path = "/" + urllib.parse.quote(key, safe="/-_.~")
    payload = hashlib.sha256(body).hexdigest()
    headers = {
        "Content-Type": content_type,
        "x-amz-content-sha256": payload,
        "x-amz-date": when.strftime("%Y%m%dT%H%M%SZ"),
    }
    headers["Authorization"] = authorization(
        method="PUT",
        host=target.host,
        path=path,
        headers={k: v for k, v in headers.items()},
        payload_sha256=payload,
        when=when,
        region=target.region,
        access_key=target.access_key,
        secret_key=target.secret_key,
    )
    return urllib.request.Request(
        f"https://{target.host}{path}", data=body, headers=headers, method="PUT"
    )


def put_object(target: S3Target, key: str, body: bytes, content_type: str) -> None:
    """Raises on anything but a 2xx (urllib's HTTPError carries S3's
    reason, e.g. AccessDenied, which the sync log shows)."""
    with urllib.request.urlopen(put_request(target, key, body, content_type), timeout=TIMEOUT_SECONDS):
        pass
