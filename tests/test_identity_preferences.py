"""saathi/identity/preferences.py — built entirely on IdentityStore's
existing create/append/read, no new method added to that interface.
`preferences` is a plain append-only log (2026-09-18 schema, no more
PRIMARY KEY on `key`) — a second write to the same key just works now;
see git history for the earlier `PreferenceLocked` behavior this
replaced, found live to break both the settings panel and the spoken
language-switch tool the moment either preference changed twice.
"""

import tempfile
from pathlib import Path

import pytest

from saathi.identity.preferences import LANGUAGE_KEY, read_preference, write_preference
from saathi.identity.store import IdentityStore


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def test_read_preference_returns_default_when_never_set(store):
    assert read_preference(store, LANGUAGE_KEY, "english") == "english"
    assert read_preference(store, LANGUAGE_KEY) is None


def test_write_then_read_preference_round_trips(store):
    write_preference(store, LANGUAGE_KEY, "chinese")
    assert read_preference(store, LANGUAGE_KEY) == "chinese"


def test_writing_the_same_key_twice_changes_the_read_value(store):
    write_preference(store, LANGUAGE_KEY, "chinese")
    write_preference(store, LANGUAGE_KEY, "hindi")
    assert read_preference(store, LANGUAGE_KEY) == "hindi"


def test_writing_the_same_key_twice_keeps_both_rows_in_the_store(store):
    # An append-only log, not an overwrite -- the first write's row is
    # still there, just no longer the one read_preference() returns.
    write_preference(store, LANGUAGE_KEY, "chinese")
    write_preference(store, LANGUAGE_KEY, "hindi")
    rows = store.read("preferences", key=LANGUAGE_KEY)
    assert len(rows) == 2
    assert {r["value"] for r in rows} == {"chinese", "hindi"}


def test_writing_the_same_key_many_times_always_reads_the_latest(store):
    for language in ("chinese", "hindi", "bengali", "english"):
        write_preference(store, LANGUAGE_KEY, language)
    assert read_preference(store, LANGUAGE_KEY) == "english"


def test_different_keys_dont_interfere(store):
    write_preference(store, "language", "chinese")
    write_preference(store, "tts_backend", "piper")
    assert read_preference(store, "language") == "chinese"
    assert read_preference(store, "tts_backend") == "piper"
