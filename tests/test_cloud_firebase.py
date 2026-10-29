"""cloud/firebase.py: the requests the REST client makes and how it reads
the replies. No network: the opener is a fake that answers from a script
and records each request."""

import datetime
import io
import json
import logging
import stat
import urllib.error
import urllib.parse

import pytest

from saathi.cloud import firebase
from saathi.cloud.firebase import (
    AlreadyExists,
    Document,
    FirebaseClient,
    FirebaseConfig,
    FirebaseError,
)

CONFIG = FirebaseConfig("AIzaKEY", "saathi-family", "saathi-family.firebasestorage.app")
DOCS = "https://firestore.googleapis.com/v1/projects/saathi-family/databases/(default)/documents"


class Reply:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, error):
    body = json.dumps({"error": error}).encode()
    return urllib.error.HTTPError("https://x", status, "Bad", {}, io.BytesIO(body))


class Opener:
    """Answers in order; each answer is a body or an exception."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Reply(answer)

    def body(self, index):
        return json.loads(self.requests[index].data)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def signed(uid="u1", token="tok1", expires="3600"):
    return {
        "idToken": token,
        "refreshToken": "ref1",
        "expiresIn": expires,
        "localId": uid,
        "email": "e@x",
    }


def _client(*answers, clock=None):
    opener = Opener(*answers)
    return FirebaseClient(CONFIG, opener=opener, monotonic=clock or Clock()), opener


# -- values -------------------------------------------------------------


def test_values_round_trip_through_firestores_encoding():
    when = datetime.datetime(2026, 10, 29, 2, 43, 5, 217000, tzinfo=datetime.timezone.utc)
    fields = {
        "s": "Ve Kangna",
        "i": 7,
        "f": 0.5,
        "b": True,
        "n": None,
        "t": when,
        "l": ["a", 1, [], {}],
        "m": {"inner": {"deep": False}},
        "raw": b"\x00\x01",
    }
    encoded = firebase.encode_fields(fields)
    assert encoded["i"] == {"integerValue": "7"}
    assert encoded["b"] == {"booleanValue": True}  # not an integer
    assert encoded["n"] == {"nullValue": None}
    assert encoded["t"] == {"timestampValue": "2026-10-29T02:43:05.217000Z"}
    assert encoded["l"]["arrayValue"]["values"][2] == {"arrayValue": {}}
    assert encoded["m"] == {
        "mapValue": {
            "fields": {"inner": {"mapValue": {"fields": {"deep": {"booleanValue": False}}}}}
        }
    }
    assert firebase.decode_fields(json.loads(json.dumps(encoded))) == fields


def test_a_nanosecond_timestamp_from_firestore_reads_as_an_aware_datetime():
    decoded = firebase.decode_value({"timestampValue": "2026-10-29T02:43:05.217123456Z"})
    assert decoded == datetime.datetime(
        2026, 10, 29, 2, 43, 5, 217123, tzinfo=datetime.timezone.utc
    )
    assert firebase.decode_value({"timestampValue": "2026-10-29T02:43:05Z"}).tzinfo is not None
    local = datetime.datetime(
        2026, 10, 29, 8, 13, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    )
    assert firebase.encode_value(local) == {"timestampValue": "2026-10-29T02:43:00.000000Z"}


def test_a_value_firestore_has_no_type_for_is_refused():
    with pytest.raises(TypeError):
        firebase.encode_value(object())


# -- config -------------------------------------------------------------


def test_all_three_names_or_no_config_and_the_emulators_from_their_standard_names():
    full = {"FIREBASE_API_KEY": "k", "FIREBASE_PROJECT_ID": "p", "FIREBASE_STORAGE_BUCKET": "b"}
    assert FirebaseConfig.from_env(full) == FirebaseConfig("k", "p", "b")
    partial = dict(full, FIREBASE_PROJECT_ID=" ")
    assert FirebaseConfig.from_env(partial) is None
    assert firebase.missing(partial) == ["FIREBASE_PROJECT_ID"]
    emulated = FirebaseConfig.from_env(
        dict(
            full,
            FIREBASE_AUTH_EMULATOR_HOST="127.0.0.1:9192",
            FIRESTORE_EMULATOR_HOST="127.0.0.1:8182",
            FIREBASE_STORAGE_EMULATOR_HOST="127.0.0.1:9292",
        )
    )
    assert emulated.auth_base == "http://127.0.0.1:9192/identitytoolkit.googleapis.com/v1"
    assert emulated.token_base == "http://127.0.0.1:9192/securetoken.googleapis.com/v1"
    assert (
        emulated.documents_base
        == "http://127.0.0.1:8182/v1/projects/p/databases/(default)/documents"
    )
    assert emulated.storage_base == "http://127.0.0.1:9292/v0/b/b/o"
    assert "SECRETKEY" not in repr(FirebaseConfig("SECRETKEY", "p", "b"))


# -- auth ---------------------------------------------------------------


def test_sign_up_and_sign_in_post_to_identity_toolkit_and_keep_the_token():
    client, opener = _client(signed("u1", "tok1"), signed("u1", "tok2"))
    account = client.sign_up("a@b.c", "pw-123456")
    assert account.uid == "u1" and client.id_token() == "tok1"
    assert (
        opener.requests[0].full_url
        == "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=AIzaKEY"
    )
    assert opener.body(0) == {"email": "a@b.c", "password": "pw-123456", "returnSecureToken": True}
    assert "Authorization" not in opener.requests[0].headers
    client.sign_in("a@b.c", "pw-123456")
    assert opener.requests[1].full_url.endswith("/accounts:signInWithPassword?key=AIzaKEY")
    assert client.id_token() == "tok2"


def test_the_token_is_cached_and_refreshed_a_minute_before_it_expires():
    clock = Clock()
    refreshed = {"id_token": "tok2", "refresh_token": "ref2", "expires_in": "3600", "user_id": "u1"}
    client, opener = _client(signed(), refreshed, clock=clock)
    client.sign_in("a@b.c", "pw")
    clock.now = 3600 - firebase.TOKEN_MARGIN_SECONDS - 1
    assert client.id_token() == "tok1" and len(opener.requests) == 1
    clock.now += 1
    assert client.id_token() == "tok2"
    refresh = opener.requests[1]
    assert refresh.full_url == "https://securetoken.googleapis.com/v1/token?key=AIzaKEY"
    assert refresh.headers["Content-type"] == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(refresh.data.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["ref1"],
    }


def test_a_refused_refresh_signs_in_again_with_the_last_credentials():
    clock = Clock()
    client, opener = _client(
        signed(token="tok1"),
        http_error(400, {"message": "TOKEN_EXPIRED"}),
        signed(token="tok3"),
        clock=clock,
    )
    client.sign_in("a@b.c", "pw")
    clock.now = 10_000
    assert client.id_token() == "tok3"
    assert opener.body(2)["password"] == "pw"


def test_errors_carry_firebases_word_and_never_the_key_or_password():
    client, _ = _client(http_error(400, {"code": 400, "message": "EMAIL_EXISTS"}))
    with pytest.raises(FirebaseError) as caught:
        client.sign_up("a@b.c", "hunter2-secret")
    assert caught.value.code == "EMAIL_EXISTS" and caught.value.status == 400
    assert "hunter2" not in str(caught.value) and "AIzaKEY" not in str(caught.value)
    client, _ = _client(
        http_error(400, {"message": "WEAK_PASSWORD : Password should be at least 6 characters"})
    )
    with pytest.raises(FirebaseError, match="WEAK_PASSWORD"):
        client.sign_up("a@b.c", "x")
    client, _ = _client(
        http_error(
            403,
            {
                "code": 403,
                "message": "Missing or insufficient permissions.",
                "status": "PERMISSION_DENIED",
            },
        )
    )
    with pytest.raises(FirebaseError, match="PERMISSION_DENIED"):
        client.get_doc("devices/d")
    # Firestore's free text can open with a capital word; the code is
    # still its status.
    client, _ = _client(
        http_error(409, {"message": "A document named d exists", "status": "ALREADY_EXISTS"})
    )
    with pytest.raises(FirebaseError) as caught:
        client.create_doc("devices", {}, doc_id="d")
    assert caught.value.code == "ALREADY_EXISTS"


# -- the device's own account --------------------------------------------


def test_the_device_signs_up_once_keeps_a_private_password_and_signs_in_after(tmp_path, caplog):
    client, opener = _client(signed("dev-uid"), signed("dev-uid"))
    with caplog.at_level(logging.DEBUG):
        account = client.ensure_device_account(tmp_path, "abc123")
    assert account.uid == "dev-uid"
    assert opener.requests[0].full_url.split("?")[0].endswith("accounts:signUp")
    assert opener.body(0)["email"] == "device-abc123@saathi.invalid"
    credential = tmp_path / "cloud" / "device-credential"
    password = credential.read_text().strip()
    assert len(password) >= 32 and opener.body(0)["password"] == password
    assert stat.S_IMODE(credential.stat().st_mode) == 0o600
    assert password not in caplog.text and password not in repr(client)
    again = FirebaseClient(CONFIG, opener=opener)
    again.ensure_device_account(tmp_path, "abc123")
    assert opener.requests[1].full_url.split("?")[0].endswith("accounts:signInWithPassword")
    assert opener.body(1)["password"] == password


def test_a_project_reset_under_the_device_signs_it_up_again_with_its_password(tmp_path):
    client, opener = _client(signed())
    client.ensure_device_account(tmp_path, "abc")
    client, opener = _client(http_error(400, {"message": "INVALID_LOGIN_CREDENTIALS"}), signed())
    client.ensure_device_account(tmp_path, "abc")
    assert opener.requests[1].full_url.split("?")[0].endswith("accounts:signUp")


def test_a_wrong_password_is_not_papered_over(tmp_path):
    client, _ = _client(signed())
    client.ensure_device_account(tmp_path, "abc")
    client, _ = _client(
        http_error(400, {"message": "INVALID_LOGIN_CREDENTIALS"}),
        http_error(400, {"message": "EMAIL_EXISTS"}),
    )
    with pytest.raises(FirebaseError, match="INVALID_LOGIN_CREDENTIALS"):
        client.ensure_device_account(tmp_path, "abc")


# -- Firestore ----------------------------------------------------------


def _doc(path, fields):
    return {
        "name": f"projects/saathi-family/databases/(default)/documents/{path}",
        "fields": firebase.encode_fields(fields),
        "updateTime": "2026-10-29T02:43:05.217Z",
    }


def test_documents_are_created_patched_and_read_with_the_bearer_token():
    client, opener = _client(
        signed(),
        _doc("devices/d1", {"caregivers": []}),
        _doc("devices/d1/turns/t1", {"heard": "hi"}),
        _doc("devices/d1", {"deviceUid": "u2", "caregivers": []}),
        _doc("devices/d1", {"deviceUid": "u2", "caregivers": []}),
        http_error(404, {"status": "NOT_FOUND", "message": "no"}),
    )
    client.sign_in("a@b.c", "pw")
    made = client.create_doc("devices", {"caregivers": []}, doc_id="d1")
    assert opener.requests[1].get_method() == "POST"
    assert opener.requests[1].full_url == f"{DOCS}/devices?documentId=d1"
    assert opener.requests[1].headers["Authorization"] == "Bearer tok1"
    assert opener.body(1) == {"fields": {"caregivers": {"arrayValue": {}}}}
    assert made == {"id": "d1", "caregivers": []} and made.path == "devices/d1"
    client.patch_doc("devices/d1/turns/t1", {"heard": "hi"})
    assert opener.requests[2].get_method() == "PATCH"
    assert opener.requests[2].full_url == f"{DOCS}/devices/d1/turns/t1"
    client.patch_doc("devices/d1", {"deviceUid": "u2"}, update_mask=["deviceUid"])
    assert opener.requests[3].full_url == f"{DOCS}/devices/d1?updateMask.fieldPaths=deviceUid"
    got = client.get_doc("devices/d1")
    assert opener.requests[4].get_method() == "GET"
    assert isinstance(got, dict) and got == {"id": "d1", "deviceUid": "u2", "caregivers": []}
    assert client.get_doc("devices/missing") is None


def test_a_query_is_one_run_query_with_the_filters_anded():
    client, opener = _client(
        [
            {"readTime": "2026-10-29T00:00:00Z"},  # no document: skipped
            {"document": _doc("pairings/p1", {"code": "123456", "used": False})},
        ]
    )
    found = client.query(
        "",
        "pairings",
        where=[("code", "==", "123456"), ("used", "==", False)],
        order_by="-createdAt",
        limit=1,
    )
    assert opener.requests[0].full_url == f"{DOCS}:runQuery"
    assert opener.body(0) == {
        "structuredQuery": {
            "from": [{"collectionId": "pairings"}],
            "where": {
                "compositeFilter": {
                    "op": "AND",
                    "filters": [
                        {
                            "fieldFilter": {
                                "field": {"fieldPath": "code"},
                                "op": "EQUAL",
                                "value": {"stringValue": "123456"},
                            }
                        },
                        {
                            "fieldFilter": {
                                "field": {"fieldPath": "used"},
                                "op": "EQUAL",
                                "value": {"booleanValue": False},
                            }
                        },
                    ],
                }
            },
            "orderBy": [{"field": {"fieldPath": "createdAt"}, "direction": "DESCENDING"}],
            "limit": 1,
        }
    }
    assert found == [{"id": "p1", "code": "123456", "used": False}]
    client, opener = _client([])
    assert client.query("devices/d1", "inbox", where=[("ts", ">", 5)]) == []
    assert opener.requests[0].full_url == f"{DOCS}/devices/d1:runQuery"
    assert opener.body(0)["structuredQuery"]["where"]["fieldFilter"]["op"] == "GREATER_THAN"
    with pytest.raises(ValueError):
        client.query("", "x", where=[("a", "~", 1)])


# -- Storage ------------------------------------------------------------


def test_an_object_is_uploaded_in_one_request_with_the_bearer_token():
    client, opener = _client(signed(), {"name": "devices/d1/audio/t 1.wav"}, b"RIFF....")
    client.sign_in("a@b.c", "pw")
    client.upload("devices/d1/audio/t 1.wav", b"RIFF....", "audio/wav")
    request = opener.requests[1]
    assert request.get_method() == "POST"
    assert request.full_url == (
        "https://firebasestorage.googleapis.com/v0/b/saathi-family.firebasestorage.app/o"
        "?name=devices%2Fd1%2Faudio%2Ft+1.wav"
    )
    assert request.headers["Authorization"] == "Bearer tok1"
    assert request.headers["Content-type"] == "audio/wav" and request.data == b"RIFF...."
    assert client.download("devices/d1/audio/t 1.wav") == b"RIFF...."
    assert opener.requests[2].full_url.endswith("/o/devices%2Fd1%2Faudio%2Ft%201.wav?alt=media")


# -- the contract the pairing and inbox code (cloud/pairing.py's
# FirestoreClient) is written against: one test per promise ------------


def test_contract_create_with_a_taken_id_raises_already_exists_never_overwrites():
    exists = {"code": 409, "message": "Document already exists: x", "status": "ALREADY_EXISTS"}
    client, opener = _client(http_error(409, exists))
    with pytest.raises(AlreadyExists) as caught:
        client.create_doc("pairing", {"deviceId": "d1"}, doc_id="123456")
    assert isinstance(caught.value, FirebaseError) and caught.value.status == 409
    # createDocument with documentId: a POST to the collection, not a PATCH
    assert opener.requests[0].get_method() == "POST"
    assert opener.requests[0].full_url == f"{DOCS}/pairing?documentId=123456"
    # Any other refusal is not mistaken for a collision.
    client, _ = _client(http_error(403, {"status": "PERMISSION_DENIED", "message": "no"}))
    with pytest.raises(FirebaseError) as caught:
        client.create_doc("pairing", {}, doc_id="1")
    assert not isinstance(caught.value, AlreadyExists)


def test_contract_patch_without_a_mask_creates_or_replaces_the_whole_document():
    client, opener = _client(_doc("devices/d1/inbox/m1", {"text": "hi"}))
    client.patch_doc("devices/d1/inbox/m1", {"text": "hi"})
    request = opener.requests[0]
    # No mask and no currentDocument precondition: Firestore creates the
    # document if missing and otherwise replaces it.
    assert request.get_method() == "PATCH"
    assert request.full_url == f"{DOCS}/devices/d1/inbox/m1"
    assert opener.body(0) == {"fields": {"text": {"stringValue": "hi"}}}


def test_contract_patch_with_a_mask_touches_only_the_listed_fields():
    client, opener = _client(_doc("devices/d1", {"deviceUid": "u", "caregivers": ["c"]}))
    client.patch_doc(
        "devices/d1", {"caregivers": ["c"], "pairedAt": 5}, update_mask=["caregivers", "pairedAt"]
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.requests[0].full_url).query)
    assert query == {"updateMask.fieldPaths": ["caregivers", "pairedAt"]}


def test_contract_order_by_a_field_name_is_ascending():
    client, opener = _client([])
    client.query("devices/d1", "inbox", order_by="createdAt")
    assert opener.body(0)["structuredQuery"]["orderBy"] == [
        {"field": {"fieldPath": "createdAt"}, "direction": "ASCENDING"}
    ]


def test_contract_docs_are_dicts_with_an_id_key_and_aware_timestamps():
    stored = {"deviceId": "d1", "expiresAt": "2026-10-29T02:53:05.217123456Z"}
    raw = _doc("pairing/123456", {"deviceId": "d1"})
    raw["fields"]["expiresAt"] = {"timestampValue": stored["expiresAt"]}
    client, _ = _client(raw, [{"document": raw}], raw)
    for doc in (client.get_doc("pairing/123456"), client.query("", "pairing")[0]):
        assert isinstance(doc, dict) and isinstance(doc, Document)
        assert doc["id"] == "123456" and doc["deviceId"] == "d1"
        assert doc["expiresAt"] == datetime.datetime(
            2026, 10, 29, 2, 53, 5, 217123, tzinfo=datetime.timezone.utc
        )
    made = client.create_doc("pairing", {"deviceId": "d1"}, doc_id="123456")
    assert made["id"] == "123456"
