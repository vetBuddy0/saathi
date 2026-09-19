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


def test_threadsafe_reader_works_from_another_thread(store):
    # Found live, not by the earlier tests: cli.py wired the preference
    # readers as lambdas over the main thread's connection, and the
    # first real end_turn() -- run in an executor thread -- crashed with
    # sqlite3's cross-thread ProgrammingError. A plain lambda over
    # `store` fails this test; threadsafe_reader must not.
    import threading

    from saathi.identity.preferences import threadsafe_reader

    write_preference(store, LANGUAGE_KEY, "chinese")
    reader = threadsafe_reader(store, LANGUAGE_KEY, "english")

    result: list = []
    errors: list = []

    def _run():
        try:
            result.append(reader())
        except Exception as exc:  # pragma: no cover - the failure this test exists to catch
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=5)
    assert errors == []
    assert result == ["chinese"]


def test_a_plain_lambda_over_the_store_really_does_fail_across_threads(store):
    # The counterexample, kept so the reason threadsafe_reader exists
    # can't be quietly forgotten: this is exactly what cli.py used to do.
    import sqlite3
    import threading

    errors: list = []

    def _run():
        try:
            read_preference(store, LANGUAGE_KEY)
        except sqlite3.ProgrammingError as exc:
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=5)
    assert len(errors) == 1


def test_different_keys_dont_interfere(store):
    write_preference(store, "language", "chinese")
    write_preference(store, "tts_backend", "piper")
    assert read_preference(store, "language") == "chinese"
    assert read_preference(store, "tts_backend") == "piper"
