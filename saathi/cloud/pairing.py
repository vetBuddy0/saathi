"""How a caregiver's phone is joined to her device: a 6-digit code.

Why it exists: the owner decided (2026-10-29) that caregivers use an
app, on Firebase, to pair with her device, send messages Saathi reads
to her, and set her reminders. Pairing is the first step and the only
one done from the device: the console's "Pair a phone" shows a code (and
a QR of `saathi-pair:{code}`); the caregiver types or scans it, and their
app adds their uid to `devices/{id}.caregivers`. The device only writes
`pairing/{code}` and reads who has claimed it.

A short code that expires, not a long token: it is read off a tablet and
typed into a phone, sometimes over the telephone to a relative. Six
digits from `secrets`, live 10 minutes. A guess has a one-in-a-million
chance against one live code, and Firestore's rules (the backend unit's)
are what stop a stream of guesses. Lost: a QR alone with a long random
token (a code you can read out loud is what makes it work for a family
spread across countries), and the device approving each phone (nobody
is at the console to approve; whoever set it up is holding the code).

The client is injected and duck-typed (`cloud/firebase.py`, a separate
unit, implements it); nothing here imports it, so tests use a fake.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)
CODE_DIGITS = 6
QR_PREFIX = "saathi-pair:"
# A fresh code that collides with one already in `pairing/` makes
# `create_doc` fail (Firestore: ALREADY_EXISTS); this many tries before
# giving up. At one live code per device in a million, more than one
# retry means the network, not bad luck.
ATTEMPTS = 3


class FirestoreClient(Protocol):
    """The slice of `cloud/firebase.py` this module and `cloud/inbox.py`
    use. Docs come back as plain dicts with an "id" key; timestamps as
    aware datetimes."""

    def create_doc(
        self, path: str, fields: dict[str, Any], doc_id: str | None = None
    ) -> dict[str, Any]: ...

    def patch_doc(
        self, path: str, fields: dict[str, Any], update_mask: list[str] | None = None
    ) -> Any: ...

    def get_doc(self, path: str) -> dict[str, Any] | None: ...

    def query(
        self,
        parent_path: str,
        collection_id: str,
        where: list[tuple[str, str, Any]] = ...,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def sign_in(self, email: str, password: str) -> Any: ...


def new_code(client: FirestoreClient, device_id: str, now: datetime) -> tuple[str, datetime]:
    """Writes `pairing/{code}` for this device and returns the code and
    when it expires. One attempt: a collision raises whatever the client
    raises, and the caller tries again (`issue_code`)."""
    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    expires_at = now + CODE_TTL
    client.create_doc("pairing", {"deviceId": device_id, "expiresAt": expires_at}, doc_id=code)
    return code, expires_at


def issue_code(
    client: FirestoreClient, device_id: str, now: datetime, attempts: int = ATTEMPTS
) -> tuple[str, datetime]:
    """`new_code`, tried again on a failure (a collision, or the network)."""
    for attempt in range(1, attempts + 1):
        try:
            return new_code(client, device_id, now)
        except Exception:
            if attempt == attempts:
                raise
            logger.warning("pairing code not written (try %d); trying another", attempt)
    raise AssertionError("unreachable")


def ensure_device(
    client: FirestoreClient,
    device_id: str,
    now: datetime,
    *,
    name: str = "Saathi",
    device_uid: str | None = None,
) -> dict[str, Any]:
    """`devices/{id}`, created the first time a phone is paired: the
    caregiver's claim adds to its `caregivers`, which needs the doc to
    exist. Returns the doc as it is now."""
    doc = client.get_doc(f"devices/{device_id}")
    if doc is not None:
        return doc
    fields: dict[str, Any] = {"name": name, "createdAt": now, "caregivers": []}
    if device_uid:
        fields["deviceUid"] = device_uid
    created = client.create_doc("devices", fields, doc_id=device_id)
    return created if isinstance(created, dict) else {"id": device_id, **fields}


def caregivers_of(doc: dict[str, Any] | None) -> list[str]:
    if not doc:
        return []
    return [uid for uid in doc.get("caregivers") or [] if isinstance(uid, str) and uid]


def paired_caregivers(client: FirestoreClient, device_id: str) -> list[str]:
    """The uids of the caregivers who have claimed a code for this device."""
    return caregivers_of(client.get_doc(f"devices/{device_id}"))
