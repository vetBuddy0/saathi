"""Reading and writing the `preferences` table through `IdentityStore`,
for exactly two keys so far: `"language"` (item G) and `"tts_backend"`
(item C) — both need "the family picks one now, changeable later" and
both need the same "write it once, read the latest" semantics, so one
small module serves both instead of each caller reinventing it.

This does **not** add a method to `IdentityStore`. Checkpoint 1's
`create`/`append`/`read` boundary is deliberate (see that module's
docstring) and `IdentityStore` is one of CLAUDE.md's five protected
interfaces — extending its surface is a conversation, not something this
module should do on its own. Everything here is built entirely on top of
`append`/`read`.

That said: `preferences.key` is currently declared `PRIMARY KEY` in
SPEC.md's schema, and `append()` is a plain `INSERT` — the *first* write
to a given key succeeds, and every write after that raises
`sqlite3.IntegrityError`, because SQLite enforces the uniqueness `append`
itself doesn't know anything about. `write_preference()` below does not
paper over that: it lets the error surface as `PreferenceLocked`, with a
message pointing at the fix (drop the `PRIMARY KEY` on `key`, read
"latest row wins" — proposed as a SPEC.md diff, not applied here). A
preference that can be set once, ever, is not what either C's settings
panel or G's spoken "speak to me in Mandarin" path asked for — showing
that honestly as a real, named exception beats quietly discarding the
second write or crashing the request handler.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from saathi.identity.store import IdentityStore

LANGUAGE_KEY = "language"
TTS_BACKEND_KEY = "tts_backend"


class PreferenceLocked(RuntimeError):
    """Raised by `write_preference()` when this key already has a row and
    the current schema's `PRIMARY KEY` on `preferences.key` refuses a
    second one. See this module's docstring for the proposed fix."""

    def __init__(self, key: str) -> None:
        super().__init__(
            f"preference {key!r} was already set once and the current schema "
            "can't record a change to it (preferences.key is a PRIMARY KEY; "
            "see saathi/identity/preferences.py's docstring for the proposed "
            "SPEC.md diff that fixes this without changing IdentityStore)."
        )
        self.key = key


def read_preference(store: IdentityStore, key: str, default: str | None = None) -> str | None:
    """The most recent value written for `key`, or `default` if it was
    never set — an empty store is an ordinary, expected state (a fresh
    install, or a family that hasn't opened the settings panel yet), not
    an error."""
    rows = store.read("preferences", key=key)
    if not rows:
        return default
    # Only ever one row per key under the current schema (see module
    # docstring) — `max` by `updated_at` is future-proofing for the day
    # the PRIMARY KEY is dropped and this legitimately becomes a log,
    # not a guess about today's behavior.
    latest = max(rows, key=lambda row: row["updated_at"])
    return latest["value"]


def write_preference(store: IdentityStore, key: str, value: str) -> None:
    """Effective on the next read — nothing here pushes the new value
    into a running session directly. `CascadeSession` (and, once G wires
    it up, the tool registry's spoken-language handler) reads through
    `read_preference()` fresh each turn for exactly this reason: "next
    turn, no restart" falls out of reading late rather than caching
    early."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        store.append("preferences", key=key, value=value, updated_at=now)
    except sqlite3.IntegrityError as exc:
        raise PreferenceLocked(key) from exc
