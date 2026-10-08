"""Web Push: how a family member's phone rings when nobody has the app open.

Exists because the family app is a web page, and a web page that isn't
open can't be told anything -- except through the browser vendor's push
service, which wakes the page's service worker. That is the whole of
"ringing" for the free family app: no account, no per-message cost, no
server of ours in the middle. The device signs a short token with its
own VAPID key (RFC 8292) and encrypts the payload to the subscription's
keys (RFC 8291, aes128gcm); the push service only sees ciphertext.

Contested: implemented here over `pycryptodomex` (~120 lines) rather
than `pywebpush`. `pywebpush` pulls `requests`, `http_ece`, `py-vapid`
and `cryptography` (four packages, one of them a native wheel not
otherwise in the default install) to do one ECDH, one HKDF, one
AES-GCM seal and one ES256 signature. `pycryptodomex` is already in the
default install (yt-dlp's extras) and is now named explicitly in
`pyproject.toml`. The encryption is pinned by RFC 8291's own worked
example in `tests/test_call_push.py`, byte for byte, so "hand-rolled"
does not mean "unchecked".

VAPID keys are generated once and kept under the data dir
(`vapid_private.pem`, mode 0600). Regenerating them invalidates every
existing subscription -- the push service ties a subscription to the
public key it was made with -- so the file is never rewritten if it
exists.

Nothing here is a payload anyone but the subscriber can read, and
nothing here is logged beyond the push service's HTTP status: an
endpoint URL identifies a person's phone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from Cryptodome.Cipher import AES
from Cryptodome.Hash import SHA256
from Cryptodome.Protocol.DH import key_agreement
from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import DSS

logger = logging.getLogger(__name__)

RECORD_SIZE = 4096
# A ring is useless a minute later; the push service drops it after this.
RING_TTL_SECONDS = 45
VAPID_FILENAME = "vapid_private.pem"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _public_bytes(key: ECC.EccKey) -> bytes:
    """Uncompressed P-256 point, 65 bytes (0x04 || X || Y)."""
    return key.public_key().export_key(format="raw")


def _import_public(raw: bytes) -> ECC.EccKey:
    return ECC.import_key(raw, curve_name="P-256")


class PushError(RuntimeError):
    """Carries an HTTP status, never the endpoint (it identifies a phone)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def gone(self) -> bool:
        """404/410: the subscription is dead (app uninstalled, permission
        revoked). The caller should forget it."""
        return self.status in (404, 410)


# -- RFC 8291 -------------------------------------------------------------


def encrypt(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    server_key: ECC.EccKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """One aes128gcm record, as RFC 8291 section 3.4 builds it.
    `server_key` and `salt` are injectable only so the RFC's own example
    can be reproduced; in use both are fresh per message."""
    server_key = server_key or ECC.generate(curve="P-256")
    salt = salt if salt is not None else os.urandom(16)
    if len(plaintext) + 1 + 16 > RECORD_SIZE:
        raise ValueError("push payload too large for one record")
    as_public = _public_bytes(server_key)
    ecdh_secret = key_agreement(
        static_priv=server_key, static_pub=_import_public(ua_public), kdf=lambda x: x
    )
    prk_key = _hmac(auth_secret, ecdh_secret)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hmac(prk_key, key_info + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    cipher = AES.new(cek, AES.MODE_GCM, nonce=nonce)
    body, tag = cipher.encrypt_and_digest(plaintext + b"\x02")
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + body + tag


# -- RFC 8292 --------------------------------------------------------------


class Vapid:
    """The device's own push identity. One per device, for ever."""

    def __init__(self, key: ECC.EccKey) -> None:
        self._key = key

    @classmethod
    def load_or_create(cls, data_dir: Path) -> "Vapid":
        path = Path(data_dir) / VAPID_FILENAME
        if path.exists():
            return cls(ECC.import_key(path.read_text()))
        key = ECC.generate(curve="P-256")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(key.export_key(format="PEM"))
        return cls(key)

    @property
    def public_key(self) -> str:
        """What the browser's `pushManager.subscribe` takes as
        `applicationServerKey`."""
        return b64url(_public_bytes(self._key))

    def token(self, endpoint: str, subject: str, now: float | None = None) -> str:
        parts = urllib.parse.urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            # 12 h, well inside the 24 h ceiling push services enforce.
            "exp": int((now if now is not None else time.time()) + 12 * 3600),
            "sub": subject,
        }
        header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        body = b64url(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = f"{header}.{body}".encode("ascii")
        signature = DSS.new(self._key, "fips-186-3").sign(SHA256.new(signing_input))
        return f"{header}.{body}.{b64url(signature)}"

    def authorization(self, endpoint: str, subject: str, now: float | None = None) -> str:
        return f"vapid t={self.token(endpoint, subject, now)}, k={self.public_key}"


# -- the message -----------------------------------------------------------


def ring_payload(call_id: str, caller: str, url: str) -> dict[str, Any]:
    """What the family member's service worker turns into a notification.
    `url` is where tapping it goes: the *current* public URL, so a ring
    still opens the right page after a quick tunnel's hostname changed
    (see `call/family_server.py`)."""
    return {
        "kind": "ring",
        "call_id": call_id,
        "title": f"Saathi — {caller}",
        "body": f"{caller} is calling you",
        "url": url,
    }


def cancel_payload(call_id: str, caller: str, missed: bool) -> dict[str, Any]:
    """Replaces the ringing notification (same tag) once the ring is
    over: answered elsewhere, ended, or missed."""
    return {
        "kind": "missed" if missed else "cancel",
        "call_id": call_id,
        "title": f"Saathi — {caller}",
        "body": f"Missed call from {caller}" if missed else "",
    }


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: bytes
    auth: bytes

    @classmethod
    def from_json(cls, data: Any) -> "Subscription":
        """The browser's `PushSubscription.toJSON()`. Raises ValueError
        on anything else -- it arrives from the internet."""
        if not isinstance(data, dict):
            raise ValueError("subscription must be an object")
        endpoint = data.get("endpoint")
        keys = data.get("keys")
        if not isinstance(endpoint, str) or not isinstance(keys, dict):
            raise ValueError("subscription needs endpoint and keys")
        parts = urllib.parse.urlsplit(endpoint)
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError("push endpoint must be https")
        try:
            p256dh = b64url_decode(str(keys.get("p256dh", "")))
            auth = b64url_decode(str(keys.get("auth", "")))
        except (ValueError, TypeError):
            raise ValueError("subscription keys are not base64url") from None
        if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) < 16:
            raise ValueError("subscription keys have the wrong shape")
        return cls(endpoint, p256dh, auth)

    def to_json(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": b64url(self.p256dh), "auth": b64url(self.auth)},
        }


# (url, body, headers, timeout) -> HTTP status. Injectable for tests.
Poster = Callable[[str, bytes, dict[str, str], float], int]


def _urllib_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError) as exc:
        raise PushError(f"push service unreachable: {type(exc).__name__}") from None


class PushSender:
    def __init__(self, vapid: Vapid, subject: str, post: Poster = _urllib_post) -> None:
        self._vapid = vapid
        self._subject = subject
        self._post = post

    @property
    def public_key(self) -> str:
        return self._vapid.public_key

    def build_request(
        self, subscription: Subscription, payload: dict[str, Any], ttl: int, urgency: str
    ) -> tuple[bytes, dict[str, str]]:
        body = encrypt(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            subscription.p256dh,
            subscription.auth,
        )
        headers = {
            "Authorization": self._vapid.authorization(subscription.endpoint, self._subject),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(int(ttl)),
            "Urgency": urgency,
            # Same topic for one call: a cancel replaces a ring still
            # queued at the push service instead of arriving after it.
            "Topic": hashlib.sha256(str(payload.get("call_id", "")).encode()).hexdigest()[:32],
        }
        return body, headers

    def send(
        self,
        subscription: Subscription,
        payload: dict[str, Any],
        *,
        ttl: int = RING_TTL_SECONDS,
        urgency: str = "high",
        timeout: float = 10.0,
    ) -> None:
        body, headers = self.build_request(subscription, payload, ttl, urgency)
        status = self._post(subscription.endpoint, body, headers, timeout)
        if not 200 <= status < 300:
            raise PushError(f"push service answered {status}", status)
