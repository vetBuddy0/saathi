"""Who is paired with the family app: names, relationships, push
subscriptions, and the one-time tokens a QR code carries.

Exists because the free family app replaces a phone number with a
pairing. A phone number is something she can say and we can store in
her memory; a pairing is a secret (a per-member key) and a push
subscription (an endpoint that identifies someone's phone), set up by
the family member, not told to us by her.

Contested: a separate SQLite file (`family.sqlite3`, beside her
identity database) owned by the call module, not `IdentityStore`.
Three reasons, each enough alone:
- `IdentityStore` has a closed schema -- `append()` raises
  `UnknownTable` outside `_SCHEMA` -- so a table there is a change to
  one of the five interfaces. The task allowed one only if it fits; it
  doesn't.
- `entities.notes` (the only free field, where a contact's phone lives)
  would fit the data, but contacts are latest-row-wins per name
  (`call/contacts.py`): a new "Priya" row carrying a pairing and no
  phone would hide Priya's saved number from every later call.
- Her memory is what she told us. A pairing is device configuration a
  family member did from their own phone, and it holds secrets that
  `identity/profile.py`'s family-visible view must never show.
The link between the two is the name, normalised the same way contacts
are (`contacts.normalise_name`): the Priya she saved and the Priya who
paired are one person to `call_contact`.

Secrets are stored hashed (SHA-256): the pairing token and each
member's key are random 32-byte values, so a plain hash is enough -- no
password stretching is needed for a value nobody chose. A token is
single-use and expires (10 minutes by default): it is shown on her
screen as a QR code, and a photo of the screen must not be a way in
tomorrow.

Threading: tool handlers, the family server's loop and the screen's
loop all read this. Every call opens its own short-lived connection
(the `identity/preferences.py:threadsafe_reader` pattern).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from saathi.call.contacts import normalise_name, normalise_relation
from saathi.call.push import Subscription

PAIRING_TTL_SECONDS = 10 * 60
MAX_NAME_CHARS = 40

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS members (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        relation TEXT,
        calls_her TEXT,
        key_hash TEXT NOT NULL,
        subscription TEXT,
        paired_at REAL NOT NULL,
        revoked_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS pairing_tokens (
        token_hash TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used_at REAL
    )""",
)


class PairingError(ValueError):
    """Said to the family member's page as-is: no secrets in the text."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    relation: str | None
    subscription: Subscription | None
    calls_her: str | None = None  # what they call her: "Mum", "Nani"

    @property
    def label(self) -> str:
        """"Priya (daughter)" -- for the settings list, never for speech."""
        return f"{self.name} ({self.relation})" if self.relation else self.name


def clean_name(text: Any) -> str:
    if not isinstance(text, str):
        raise PairingError("Please type your name.")
    name = " ".join(text.split())[:MAX_NAME_CHARS]
    if not name or not any(ch.isalpha() for ch in name):
        raise PairingError("Please type your name.")
    return name


class FamilyRegistry:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self, fn):
        conn = self._connect()
        try:
            with conn:
                return fn(conn)
        finally:
            conn.close()

    # -- pairing tokens ------------------------------------------------------

    def issue_pairing_token(self, ttl: float = PAIRING_TTL_SECONDS) -> tuple[str, float]:
        """A fresh one-time token and when it stops working. Any earlier
        unused token is left to expire on its own: two family members
        pairing from the same screen a minute apart both work."""
        token = secrets.token_urlsafe(24)
        now = self._clock()
        self._run(
            lambda c: c.execute(
                "INSERT INTO pairing_tokens VALUES (?, ?, ?, NULL)",
                (_hash(token), now, now + ttl),
            )
        )
        return token, now + ttl

    def redeem(
        self, token: Any, name: Any, relation: Any = None, calls_her: Any = None
    ) -> tuple[Member, str]:
        """Spends the token and pairs a member. Returns the member and
        their key -- the key is shown exactly once, to their page, and
        only its hash is kept. Raises `PairingError`."""
        if not isinstance(token, str) or not token:
            raise PairingError("This pairing link isn't valid. Ask for a new code.")
        clean = clean_name(name)
        rel = normalise_relation(relation[:MAX_NAME_CHARS]) if isinstance(relation, str) else None
        her = " ".join(calls_her.split())[:MAX_NAME_CHARS] if isinstance(calls_her, str) else ""
        her = her or None
        now = self._clock()
        key = secrets.token_urlsafe(32)
        member_id = secrets.token_hex(8)

        def spend(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT expires_at, used_at FROM pairing_tokens WHERE token_hash = ?",
                (_hash(token),),
            ).fetchone()
            if row is None:
                raise PairingError("This pairing link isn't valid. Ask for a new code.")
            if row["used_at"] is not None:
                raise PairingError("This code has already been used. Ask for a new one.")
            if now >= row["expires_at"]:
                raise PairingError("This code has expired. Ask for a new one.")
            conn.execute(
                "UPDATE pairing_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (now, _hash(token)),
            )
            conn.execute(
                "INSERT INTO members (id, name, relation, calls_her, key_hash, subscription,"
                " paired_at, revoked_at) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)",
                (member_id, clean, rel, her, _hash(key), now),
            )

        # BEGIN IMMEDIATE: two pages racing the same token can't both
        # read "unused" before either writes.
        conn = self._connect()
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            try:
                spend(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()
        return Member(member_id, clean, rel, None, her), key

    # -- members -------------------------------------------------------------

    def authenticate(self, member_id: Any, key: Any) -> Member | None:
        if not isinstance(member_id, str) or not isinstance(key, str):
            return None
        row = self._run(
            lambda c: c.execute(
                "SELECT * FROM members WHERE id = ? AND revoked_at IS NULL", (member_id,)
            ).fetchone()
        )
        if row is None or not hmac.compare_digest(row["key_hash"], _hash(key)):
            return None
        return _member(row)

    def set_subscription(self, member_id: str, subscription: Subscription | None) -> None:
        data = json.dumps(subscription.to_json()) if subscription is not None else None
        self._run(
            lambda c: c.execute(
                "UPDATE members SET subscription = ? WHERE id = ?", (data, member_id)
            )
        )

    def revoke(self, member_id: str) -> bool:
        now = self._clock()
        cursor = self._run(
            lambda c: c.execute(
                "UPDATE members SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, member_id),
            )
        )
        return cursor.rowcount > 0

    def members(self) -> list[Member]:
        """Paired and not revoked, latest pairing per name: re-pairing a
        new phone replaces the old one for calling, the way a new number
        does for contacts."""
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM members WHERE revoked_at IS NULL ORDER BY paired_at, rowid"
            ).fetchall()
        )
        latest: dict[str, Member] = {}
        for row in rows:
            latest[normalise_name(row["name"])] = _member(row)
        return list(latest.values())

    def get(self, member_id: str) -> Member | None:
        row = self._run(
            lambda c: c.execute(
                "SELECT * FROM members WHERE id = ? AND revoked_at IS NULL", (member_id,)
            ).fetchone()
        )
        return _member(row) if row is not None else None

    def find_by_name(self, name: str) -> Member | None:
        key = normalise_name(name)
        return next((m for m in self.members() if normalise_name(m.name) == key), None)

    def find_by_relation(self, relation: str) -> Member | None:
        canonical = normalise_relation(relation)
        if canonical is None:
            return None
        found = [m for m in self.members() if m.relation == canonical]
        return found[-1] if found else None


def _member(row: sqlite3.Row) -> Member:
    subscription = None
    if row["subscription"]:
        try:
            subscription = Subscription.from_json(json.loads(row["subscription"]))
        except ValueError:
            subscription = None
    return Member(row["id"], row["name"], row["relation"], subscription, row["calls_her"])

