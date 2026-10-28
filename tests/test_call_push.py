"""call/push.py -- Web Push without pywebpush. The encryption is pinned
to RFC 8291's own worked example, byte for byte; the VAPID token is
verified with the public key it advertises; a sent request is decrypted
the way a browser would."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat

import pytest
from Cryptodome.Cipher import AES
from Cryptodome.Hash import SHA256
from Cryptodome.Protocol.DH import key_agreement
from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import DSS

from saathi.call import push
from saathi.call.push import (
    PushError,
    PushSender,
    Subscription,
    Vapid,
    b64url,
    b64url_decode,
    cancel_payload,
    encrypt,
    ring_payload,
)

# RFC 8291, section 5 / appendix A.
RFC_PLAINTEXT = b"When I grow up, I want to be a watermelon"
RFC_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tl"
    "zAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def test_encryption_matches_rfc_8291_example_exactly():
    key = ECC.construct(curve="P-256", d=int.from_bytes(b64url_decode(RFC_AS_PRIVATE), "big"))
    body = encrypt(
        RFC_PLAINTEXT,
        b64url_decode(RFC_UA_PUBLIC),
        b64url_decode(RFC_AUTH),
        server_key=key,
        salt=b64url_decode(RFC_SALT),
    )
    assert b64url(body) == RFC_BODY


def _decrypt(body: bytes, ua_key: ECC.EccKey, auth: bytes) -> bytes:
    """The receiving browser's side of RFC 8291, written independently."""
    salt, rs, idlen = body[:16], body[16:20], body[20]
    as_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    assert int.from_bytes(rs, "big") == 4096
    ua_public = ua_key.public_key().export_key(format="raw")
    secret = key_agreement(
        static_priv=ua_key,
        static_pub=ECC.import_key(as_public, curve_name="P-256"),
        kdf=lambda x: x,
    )

    def mac(k, d):
        return hmac.new(k, d, hashlib.sha256).digest()

    ikm = mac(mac(auth, secret), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = mac(salt, ikm)
    cek = mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = mac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AES.new(cek, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(
        ciphertext[:-16], ciphertext[-16:]
    )
    assert plain.endswith(b"\x02")
    return plain[:-1]


def _subscriber():
    key = ECC.generate(curve="P-256")
    auth = os.urandom(16)
    sub = Subscription(
        "https://push.example.test/send/abc123",
        key.public_key().export_key(format="raw"),
        auth,
    )
    return key, auth, sub


def test_a_sent_ring_is_decryptable_and_carries_vapid_and_urgency():
    ua_key, auth, sub = _subscriber()
    vapid = Vapid(ECC.generate(curve="P-256"))
    sent = []

    def post(url, body, headers, timeout):
        sent.append((url, body, headers))
        return 201

    sender = PushSender(vapid, "mailto:owner@example.test", post=post)
    payload = ring_payload("c1", "Mum", "https://saathi.example.test/family/#call=c1")
    sender.send(sub, payload)
    url, body, headers = sent[0]
    assert url == sub.endpoint
    assert json.loads(_decrypt(body, ua_key, auth)) == payload
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["TTL"] == str(push.RING_TTL_SECONDS)
    assert headers["Urgency"] == "high"
    assert headers["Authorization"].startswith("vapid t=")
    assert headers["Authorization"].endswith(f", k={vapid.public_key}")


def test_the_vapid_token_verifies_with_the_advertised_key_and_names_the_push_origin():
    vapid = Vapid(ECC.generate(curve="P-256"))
    token = vapid.token("https://fcm.googleapis.com/fcm/send/xyz", "mailto:a@b.test", now=1000)
    header, claims, signature = token.split(".")
    assert json.loads(b64url_decode(header)) == {"typ": "JWT", "alg": "ES256"}
    body = json.loads(b64url_decode(claims))
    assert body["aud"] == "https://fcm.googleapis.com"
    assert body["sub"] == "mailto:a@b.test"
    assert 0 < body["exp"] - 1000 <= 24 * 3600  # push services refuse more than a day
    public = ECC.import_key(b64url_decode(vapid.public_key), curve_name="P-256")
    DSS.new(public, "fips-186-3").verify(
        SHA256.new(f"{header}.{claims}".encode()), b64url_decode(signature)
    )


def test_vapid_keys_are_made_once_private_to_the_user_and_never_rewritten(tmp_path):
    first = Vapid.load_or_create(tmp_path)
    path = tmp_path / push.VAPID_FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = Vapid.load_or_create(tmp_path)
    assert again.public_key == first.public_key  # regenerating would orphan every phone


def test_a_dead_subscription_is_reported_as_gone_and_never_names_the_endpoint():
    _, _, sub = _subscriber()
    sender = PushSender(Vapid(ECC.generate(curve="P-256")), "mailto:x@y.test",
                        post=lambda *a: 410)
    with pytest.raises(PushError) as caught:
        sender.send(sub, ring_payload("c", "Mum", "https://x.test/family/"))
    assert caught.value.gone
    assert "push.example.test" not in str(caught.value)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {"endpoint": "http://insecure.test/x", "keys": {"p256dh": "AA", "auth": "AA"}},
        {"endpoint": "https://p.test/x", "keys": {"p256dh": "AAAA", "auth": "BBBB"}},
        {"endpoint": "https://p.test/x"},
    ],
)
def test_a_subscription_from_the_internet_is_validated(bad):
    with pytest.raises(ValueError):
        Subscription.from_json(bad)


def test_a_subscription_round_trips_through_json():
    _, _, sub = _subscriber()
    assert Subscription.from_json(sub.to_json()) == sub


def test_payloads_say_who_is_calling_and_where_to_go():
    ring = ring_payload("c9", "Nani", "https://s.test/family/#call=c9")
    assert ring == {
        "kind": "ring",
        "call_id": "c9",
        "title": "Kaki — Nani",
        "body": "Nani is calling you",
        "url": "https://s.test/family/#call=c9",
    }
    missed = cancel_payload("c9", "Nani", missed=True)
    assert missed["kind"] == "missed" and missed["body"] == "Missed call from Nani"
