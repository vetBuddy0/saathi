"""Firebase Auth, Firestore and Storage over their REST APIs, on urllib.

Why it exists: the owner decided (2026-10-29) "use Firebase for
everything, don't use AWS". The device's copy of each exchange, her
clip and the memory file now goes to the owner's Firebase project, and
a caregiver app reads it there. On a tablet the engine runs embedded
through Chaquopy, so this is the standard library only.

Lost:
- firebase-admin / google-cloud-firestore. They speak gRPC, which has
  no Chaquopy wheel, and they are tens of MB for a handful of requests.
- A service account on the device. A service account bypasses every
  security rule; a key on a tablet that can be stolen is a key to every
  family's data. Never allowed. The device signs in as its own Firebase
  Auth user instead (`ensure_device_account`), and the rules decide
  what that user may touch.
- Keeping S3 alongside. No AWS keys were ever entered, so there was
  nothing to migrate, and two clouds is two sets of keys to lose.

The seam other code uses (the pairing/inbox unit codes against these
names and signatures, duck-typed; keep them stable):

    sign_in(email, password) -> Account
    create_doc(path, fields, doc_id=None) -> Document
        `path` is a collection ("devices/abc/turns"). With no doc_id
        Firestore picks one. Raises AlreadyExists (a FirebaseError, 409
        ALREADY_EXISTS) if doc_id is taken: pairing finds code
        collisions this way, so it must never overwrite.
    patch_doc(path, fields, update_mask=None) -> Document
        `path` is a document. With no mask the document becomes exactly
        `fields` (created if missing); with a mask only those field
        paths change.
    get_doc(path) -> Document | None
        None when the document does not exist.
    query(parent_path, collection_id, where=[(field, op, value)],
          order_by=None, limit=None) -> list[Document]
        `parent_path` is "" for a top-level collection. `op` is one of
        ==, !=, <, <=, >, >=, array-contains, array-contains-any, in,
        not-in; several conditions are ANDed. `order_by` is a field
        name, "-field" for descending, or a list of those.

A `Document` is a plain dict of decoded fields (str, int, float, bool,
None, aware datetime, list, dict, bytes) plus an "id" key holding the
document's id (a stored field named "id" is shadowed by it). It also has
`.id`, `.path` (relative to `documents/`) and `.update_time` attributes.
Lost: the bare field dict with the id only as an attribute; the pairing
and inbox code reads `doc["id"]`, as a plain dict would carry it.

Requests carry the signed-in user's ID token as `Bearer`. The token is
cached until a minute before it expires, then refreshed through
securetoken; if the refresh is refused, the last credentials are used to
sign in again. The emulators are used when the standard
`FIREBASE_AUTH_EMULATOR_HOST`, `FIRESTORE_EMULATOR_HOST` and
`FIREBASE_STORAGE_EMULATOR_HOST` are set.

No password, token or API key is ever logged or put in an exception
message: errors carry the HTTP status and Firebase's own error code
(e.g. EMAIL_EXISTS, PERMISSION_DENIED).
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# FIREBASE_ throughout, so the Android shell's key store passes the set
# through one prefix (saathi/android.py's KEY_PREFIXES).
ENV_NAMES = ("FIREBASE_API_KEY", "FIREBASE_PROJECT_ID", "FIREBASE_STORAGE_BUCKET")
AUTH_EMULATOR_VAR = "FIREBASE_AUTH_EMULATOR_HOST"
FIRESTORE_EMULATOR_VAR = "FIRESTORE_EMULATOR_HOST"
STORAGE_EMULATOR_VAR = "FIREBASE_STORAGE_EMULATOR_HOST"

TIMEOUT_SECONDS = 30
# Refresh this long before Firebase says the token expires, so a request
# never leaves with a token that dies on the way.
TOKEN_MARGIN_SECONDS = 60
DEVICE_EMAIL = "device-{id}@saathi.invalid"
CREDENTIAL_FILE = Path("cloud") / "device-credential"

_OPS = {
    "==": "EQUAL",
    "!=": "NOT_EQUAL",
    "<": "LESS_THAN",
    "<=": "LESS_THAN_OR_EQUAL",
    ">": "GREATER_THAN",
    ">=": "GREATER_THAN_OR_EQUAL",
    "array-contains": "ARRAY_CONTAINS",
    "array-contains-any": "ARRAY_CONTAINS_ANY",
    "in": "IN",
    "not-in": "NOT_IN",
}


class FirebaseError(Exception):
    """An HTTP error from Firebase. `code` is Firebase's own word for it
    (EMAIL_EXISTS, ALREADY_EXISTS, PERMISSION_DENIED, ...), or the HTTP
    reason when the body had none."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


class AlreadyExists(FirebaseError):
    """`create_doc` with a `doc_id` that is taken (Firestore's 409
    ALREADY_EXISTS)."""


@dataclass(frozen=True)
class FirebaseConfig:
    # The web API key identifies the project, it grants nothing by
    # itself; kept out of repr all the same.
    api_key: str = field(repr=False)
    project_id: str
    storage_bucket: str
    auth_emulator: str | None = None
    firestore_emulator: str | None = None
    storage_emulator: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> FirebaseConfig | None:
        values = [env.get(name, "").strip() for name in ENV_NAMES]
        if not all(values):
            return None

        def host(name: str) -> str | None:
            return env.get(name, "").strip() or None

        return cls(
            *values,
            auth_emulator=host(AUTH_EMULATOR_VAR),
            firestore_emulator=host(FIRESTORE_EMULATOR_VAR),
            storage_emulator=host(STORAGE_EMULATOR_VAR),
        )

    @property
    def auth_base(self) -> str:
        if self.auth_emulator:
            return f"http://{self.auth_emulator}/identitytoolkit.googleapis.com/v1"
        return "https://identitytoolkit.googleapis.com/v1"

    @property
    def token_base(self) -> str:
        if self.auth_emulator:
            return f"http://{self.auth_emulator}/securetoken.googleapis.com/v1"
        return "https://securetoken.googleapis.com/v1"

    @property
    def documents_base(self) -> str:
        host = (
            f"http://{self.firestore_emulator}"
            if self.firestore_emulator
            else "https://firestore.googleapis.com"
        )
        return f"{host}/v1/projects/{self.project_id}/databases/(default)/documents"

    @property
    def storage_base(self) -> str:
        host = (
            f"http://{self.storage_emulator}"
            if self.storage_emulator
            else "https://firebasestorage.googleapis.com"
        )
        return f"{host}/v0/b/{self.storage_bucket}/o"


def missing(env: Mapping[str, str]) -> list[str]:
    return [name for name in ENV_NAMES if not env.get(name, "").strip()]


@dataclass(frozen=True)
class Account:
    uid: str
    email: str


class Document(dict):
    """Decoded fields, plus where the document lives."""

    def __init__(
        self,
        fields: Mapping[str, Any],
        *,
        id: str = "",
        path: str = "",
        update_time: str | None = None,
    ):
        super().__init__(fields)
        self["id"] = id
        self.id = id
        self.path = path
        self.update_time = update_time


# -- Firestore values ---------------------------------------------------


def encode_value(value: Any) -> dict:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):  # before int: bool is an int
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, datetime.datetime):
        return {"timestampValue": _rfc3339(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"bytesValue": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [encode_value(item) for item in value]} if value else {}}
    if isinstance(value, Mapping):
        return {"mapValue": {"fields": encode_fields(value)} if value else {}}
    raise TypeError(f"no Firestore value for {type(value).__name__}")


def encode_fields(fields: Mapping[str, Any]) -> dict:
    return {str(name): encode_value(value) for name, value in fields.items()}


def decode_value(value: Mapping[str, Any]) -> Any:
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])  # also "NaN" / "Infinity"
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        return _parse_timestamp(value["timestampValue"])
    if "bytesValue" in value:
        return base64.b64decode(value["bytesValue"])
    if "arrayValue" in value:
        return [decode_value(item) for item in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return decode_fields(value["mapValue"].get("fields", {}))
    if "referenceValue" in value:
        return value["referenceValue"]
    if "geoPointValue" in value:
        return dict(value["geoPointValue"])
    raise ValueError(f"unknown Firestore value {sorted(value)}")


def decode_fields(fields: Mapping[str, Any]) -> dict:
    return {name: decode_value(value) for name, value in fields.items()}


def _rfc3339(when: datetime.datetime) -> str:
    """A naive datetime is taken as UTC: everything the engine stamps is
    UTC, and Firestore has no "local"."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    when = when.astimezone(datetime.timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond:06d}Z"


def _parse_timestamp(text: str) -> datetime.datetime:
    """Firestore sends up to nanoseconds; Python keeps microseconds."""
    main, _, rest = text.partition(".")
    if rest:
        digits = "".join(ch for ch in rest if ch.isdigit())
        zone = rest[len(digits) :]
        text = f"{main}.{digits[:6].ljust(6, '0')}{zone}"
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _document(raw: Mapping[str, Any]) -> Document:
    name = raw.get("name", "")
    _, _, path = name.partition("/documents/")
    return Document(
        decode_fields(raw.get("fields", {})),
        id=path.rsplit("/", 1)[-1],
        path=path,
        update_time=raw.get("updateTime"),
    )


def _quote_path(path: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in path.strip("/").split("/"))


def _order(spec: str | tuple[str, str]) -> dict:
    if isinstance(spec, tuple):
        name, direction = spec
        descending = direction.lower().startswith("desc")
    else:
        descending = spec.startswith("-")
        name = spec.lstrip("-")
    return {"field": {"fieldPath": name}, "direction": "DESCENDING" if descending else "ASCENDING"}


# -- the client ---------------------------------------------------------

Opener = Callable[..., Any]


class FirebaseClient:
    """One signed-in user's view of one Firebase project. Thread-safe:
    the cloud sync worker and the pairing code share one client, and so
    one token."""

    def __init__(
        self,
        config: FirebaseConfig,
        *,
        opener: Opener = urllib.request.urlopen,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._open = opener
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._credentials: tuple[str, str] | None = None
        self.account: Account | None = None

    def __repr__(self) -> str:
        who = self.account.email if self.account else "nobody"
        return f"FirebaseClient({self.config.project_id!r}, signed in as {who})"

    # -- HTTP ----------------------------------------------------------

    def _call(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        auth: bool = True,
        parse: bool = True,
    ) -> Any:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        if auth:
            token = self.id_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            code = _error_code(exc)
            if exc.code == 409 and code == "ALREADY_EXISTS":
                raise AlreadyExists(exc.code, code) from None
            raise FirebaseError(exc.code, code) from None
        if not parse:
            return raw
        return json.loads(raw) if raw else None

    def _json(self, method: str, url: str, payload: Any, *, auth: bool = True) -> Any:
        return self._call(
            method,
            url,
            body=json.dumps(payload).encode(),
            content_type="application/json",
            auth=auth,
        )

    # -- Auth ----------------------------------------------------------

    def sign_up(self, email: str, password: str) -> Account:
        return self._password_call("accounts:signUp", email, password)

    def sign_in(self, email: str, password: str) -> Account:
        return self._password_call("accounts:signInWithPassword", email, password)

    def _password_call(self, action: str, email: str, password: str) -> Account:
        key = urllib.parse.quote(self.config.api_key, safe="")
        reply = self._json(
            "POST",
            f"{self.config.auth_base}/{action}?key={key}",
            {"email": email, "password": password, "returnSecureToken": True},
            auth=False,
        )
        with self._lock:
            self._credentials = (email, password)
            self._take_tokens(
                reply["idToken"], reply["refreshToken"], reply.get("expiresIn", "3600")
            )
            self.account = Account(uid=reply["localId"], email=reply.get("email", email))
            return self.account

    def _take_tokens(self, id_token: str, refresh_token: str, expires_in: str | int) -> None:
        self._id_token = id_token
        self._refresh_token = refresh_token
        self._expires_at = self._monotonic() + int(expires_in) - TOKEN_MARGIN_SECONDS

    def refresh(self) -> None:
        """A new ID token from the refresh token (securetoken). If that is
        refused (the refresh token was revoked), sign in again with the
        last credentials."""
        with self._lock:
            if self._refresh_token is None:
                raise FirebaseError(401, "NOT_SIGNED_IN")
            key = urllib.parse.quote(self.config.api_key, safe="")
            form = urllib.parse.urlencode(
                {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
            )
            try:
                reply = self._call(
                    "POST",
                    f"{self.config.token_base}/token?key={key}",
                    body=form.encode(),
                    content_type="application/x-www-form-urlencoded",
                    auth=False,
                )
            except FirebaseError as exc:
                if exc.status != 400 or self._credentials is None:
                    raise
                self.sign_in(*self._credentials)
                return
            self._take_tokens(
                reply["id_token"], reply["refresh_token"], reply.get("expires_in", "3600")
            )

    def id_token(self) -> str | None:
        """The cached ID token, refreshed when it is about to expire;
        None when nobody has signed in (requests then go unauthenticated)."""
        with self._lock:
            if self._id_token is None:
                return None
            if self._monotonic() >= self._expires_at:
                self.refresh()
            return self._id_token

    def ensure_device_account(self, data_dir: Path, device_id: str) -> Account:
        """Sign in as this device's own user, signing it up the first
        time. The password is random, made once, and kept in
        `<data_dir>/cloud/device-credential` (0600): written before the
        sign-up, so a crash between the two never leaves an account
        nobody can sign in to."""
        email = DEVICE_EMAIL.format(id=device_id)
        path = Path(data_dir) / CREDENTIAL_FILE
        password = _read_credential(path)
        if password is not None:
            try:
                return self.sign_in(email, password)
            except FirebaseError as exc:
                # A project with email-enumeration protection (the
                # default since 2023) says INVALID_LOGIN_CREDENTIALS for
                # an unknown email too.
                if exc.code not in ("EMAIL_NOT_FOUND", "INVALID_LOGIN_CREDENTIALS"):
                    raise
                refused = exc
            # The project was reset under a device that kept its file.
            try:
                return self.sign_up(email, password)
            except FirebaseError as exc:
                if exc.code == "EMAIL_EXISTS":
                    raise refused from None
                raise
        password = secrets.token_urlsafe(32)
        _write_credential(path, password)
        try:
            return self.sign_up(email, password)
        except FirebaseError as exc:
            if exc.code == "EMAIL_EXISTS":
                raise FirebaseError(
                    exc.status,
                    "EMAIL_EXISTS: this device's account exists but its credential file was lost; "
                    "delete the user in the Firebase console to start again",
                ) from None
            raise

    # -- Firestore -----------------------------------------------------

    def create_doc(
        self, path: str, fields: Mapping[str, Any], doc_id: str | None = None
    ) -> Document:
        url = f"{self.config.documents_base}/{_quote_path(path)}"
        if doc_id is not None:
            url += "?" + urllib.parse.urlencode({"documentId": doc_id})
        return _document(self._json("POST", url, {"fields": encode_fields(fields)}))

    def patch_doc(
        self, path: str, fields: Mapping[str, Any], update_mask: list[str] | None = None
    ) -> Document:
        url = f"{self.config.documents_base}/{_quote_path(path)}"
        if update_mask is not None:
            url += "?" + urllib.parse.urlencode(
                [("updateMask.fieldPaths", name) for name in update_mask]
            )
        return _document(self._json("PATCH", url, {"fields": encode_fields(fields)}))

    def get_doc(self, path: str) -> Document | None:
        try:
            raw = self._call("GET", f"{self.config.documents_base}/{_quote_path(path)}")
        except FirebaseError as exc:
            if exc.status == 404:
                return None
            raise
        return _document(raw)

    def query(
        self,
        parent_path: str,
        collection_id: str,
        where: list[tuple[str, str, Any]] | None = None,
        order_by: str | tuple[str, str] | list | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        structured: dict[str, Any] = {"from": [{"collectionId": collection_id}]}
        filters = []
        for name, op, value in where or []:
            if op not in _OPS:
                raise ValueError(f"unknown query operator {op!r}")
            filters.append(
                {
                    "fieldFilter": {
                        "field": {"fieldPath": name},
                        "op": _OPS[op],
                        "value": encode_value(value),
                    }
                }
            )
        if len(filters) == 1:
            structured["where"] = filters[0]
        elif filters:
            structured["where"] = {"compositeFilter": {"op": "AND", "filters": filters}}
        if order_by is not None:
            specs = order_by if isinstance(order_by, list) else [order_by]
            structured["orderBy"] = [_order(spec) for spec in specs]
        if limit is not None:
            structured["limit"] = int(limit)
        parent = _quote_path(parent_path) if parent_path.strip("/") else ""
        url = self.config.documents_base + (f"/{parent}" if parent else "") + ":runQuery"
        rows = self._json("POST", url, {"structuredQuery": structured}) or []
        return [_document(row["document"]) for row in rows if "document" in row]

    # -- Storage -------------------------------------------------------

    def upload(self, object_path: str, body: bytes, content_type: str) -> dict:
        """One object, in one request. Returns Firebase's metadata."""
        url = f"{self.config.storage_base}?" + urllib.parse.urlencode({"name": object_path})
        return self._call("POST", url, body=body, content_type=content_type)

    def download(self, object_path: str) -> bytes:
        url = f"{self.config.storage_base}/{urllib.parse.quote(object_path, safe='')}?alt=media"
        return self._call("GET", url, parse=False)


def _error_code(exc: urllib.error.HTTPError) -> str:
    """Firebase's error word from the body: Auth puts it in
    error.message ("EMAIL_EXISTS"), Firestore in error.status
    ("ALREADY_EXISTS"). Never the request: its URL carries the key."""
    try:
        error = json.loads(exc.read()).get("error", {})
    except Exception:
        return str(exc.reason)
    if not isinstance(error, dict):
        return str(error)[:160]
    # Auth's message is the bare word, or "WORD : explanation". Firestore
    # and Storage write free text there ("A document ...", "ID token ..."),
    # so a capitalised first word alone is not a code.
    message = str(error.get("message", ""))
    word, _, rest = message.partition(" ")
    if word.replace("_", "").isalnum() and word.isupper() and (not rest or rest.startswith(": ")):
        return word
    return str(error.get("status") or message or exc.reason)[:160]


def _read_credential(path: Path) -> str | None:
    try:
        text = path.read_text().strip()
    except FileNotFoundError:
        return None
    return text or None


def _write_credential(path: Path, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(password + "\n")
    os.replace(temporary, path)
