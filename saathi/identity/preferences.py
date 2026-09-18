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

`preferences.key` used to be a `PRIMARY KEY`, which made the *first*
write to a key succeed and every write after that raise
`sqlite3.IntegrityError` — found live, breaking both C's settings panel
and G's spoken "speak to me in Mandarin" path the moment either
preference was changed a second time. Fixed directly in the schema
(SPEC.md and `identity/store.py`, 2026-09-18, with existing rows
migrated in place — see `identity/store.py`'s `_migrate_preferences`):
`preferences` is a plain append-only log now, same as every other table
`IdentityStore` holds, and `write_preference()` is a bare `append()`
with nothing to catch.
"""

from __future__ import annotations

from datetime import datetime, timezone

from saathi.identity.store import IdentityStore

LANGUAGE_KEY = "language"
TTS_BACKEND_KEY = "tts_backend"


def read_preference(store: IdentityStore, key: str, default: str | None = None) -> str | None:
    """The most recent value written for `key`, or `default` if it was
    never set — an empty store is an ordinary, expected state (a fresh
    install, or a family that hasn't opened the settings panel yet), not
    an error. Ties on `updated_at` (two writes landing in the same
    microsecond — plausible in a tight loop, e.g. tests) break on `id`,
    which is monotonic with insertion order and never ties, rather than
    on `max()`'s undefined-in-practice "first row seen" tiebreak."""
    rows = store.read("preferences", key=key)
    if not rows:
        return default
    latest = max(rows, key=lambda row: (row["updated_at"], row["id"]))
    return latest["value"]


def write_preference(store: IdentityStore, key: str, value: str) -> None:
    """Effective on the next read — nothing here pushes the new value
    into a running session directly. `CascadeSession` (via
    `language_preference`/`backend_preference`) and the `set_language`
    tool both read through `read_preference()` fresh each turn for
    exactly this reason: "next turn, no restart" falls out of reading
    late rather than caching early."""
    now = datetime.now(timezone.utc).isoformat()
    store.append("preferences", key=key, value=value, updated_at=now)
