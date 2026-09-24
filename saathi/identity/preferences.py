"""Reading and writing the `preferences` table through `IdentityStore`,
for three keys: `"language"` (item G), `"tts_backend"` (item C) and
`"voice"` (the paired-voice picker, which supersedes `tts_backend` as
the thing that decides what she sounds like -- `tts_backend` is still
read, as a fallback, so an older device's choice survives). All need
"the family picks one now, changeable later" and the same "write it
once, read the latest" semantics, so one small module serves them
instead of each caller reinventing it.

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
from typing import Callable

from saathi.identity.store import IdentityStore
from saathi.voice.tts.voices import DEFAULT_VOICE_ID, voice_by_id, voice_from_legacy_backend

LANGUAGE_KEY = "language"
TTS_BACKEND_KEY = "tts_backend"
VOICE_KEY = "voice"


def read_voice_preference(store: IdentityStore) -> str:
    """The id of the voice pair she should speak with -- always a real
    id from `voices.VOICES`, never `None`, so callers don't each carry a
    fallback. Order: the `voice` preference if it names a pair that
    still exists; else the pre-picker `tts_backend` preference mapped to
    that backend's pair (a device that chose Chirp before voices existed
    keeps Chirp); else `DEFAULT_VOICE_ID`. A `voice` row naming a pair
    that was later removed from the list falls through rather than
    raising: an operator's stale choice is not an error at turn time."""
    chosen = read_preference(store, VOICE_KEY)
    if voice_by_id(chosen) is not None:
        return chosen  # type: ignore[return-value]  # voice_by_id proved it's a str
    legacy = read_preference(store, TTS_BACKEND_KEY)
    if legacy is not None:
        return voice_from_legacy_backend(legacy)
    return DEFAULT_VOICE_ID


def threadsafe_voice_reader(store: IdentityStore) -> Callable[[], str]:
    """`threadsafe_reader` for the voice, with `read_voice_preference`'s
    fallback chain instead of a single key -- see that function for why
    every call opens its own connection."""
    path = store.path

    def _read() -> str:
        with IdentityStore(path) as own:
            return read_voice_preference(own)

    return _read


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


def threadsafe_reader(
    store: IdentityStore, key: str, default: str | None = None
) -> Callable[[], str | None]:
    """A zero-arg reader for `key` that is safe to call from *any*
    thread — `CascadeSession` calls its preference readers from the
    executor thread `end_turn()` runs in, and from the daemon thread
    that warms the voice at construction, never from the thread that
    opened `store`. `sqlite3` connections can't cross threads
    (`ProgrammingError`), and this was found live, not in tests: the
    very first spacebar release of a real run crashed the turn, while
    every test passed `lambda: "fake"` and never touched a store from
    another thread. Opens its own short-lived connection to the same
    file per call (SQLite's own supported way — see
    `IdentityStore.path`), which is one tiny read per turn."""
    path = store.path

    def _read() -> str | None:
        with IdentityStore(path) as own:
            return read_preference(own, key, default)

    return _read


def write_preference(store: IdentityStore, key: str, value: str) -> None:
    """Effective on the next read — nothing here pushes the new value
    into a running session directly. `CascadeSession` (via
    `language_preference`/`backend_preference`) and the `set_language`
    tool both read through `read_preference()` fresh each turn for
    exactly this reason: "next turn, no restart" falls out of reading
    late rather than caching early."""
    now = datetime.now(timezone.utc).isoformat()
    store.append("preferences", key=key, value=value, updated_at=now)
