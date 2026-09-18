"""saathi/identity/preferences.py — built entirely on IdentityStore's
existing create/append/read, no new method added to that interface. The
`PreferenceLocked` tests pin down a real, current limitation (see that
module's docstring): a second write to the same key fails until
preferences.key's PRIMARY KEY is dropped per the proposed SPEC.md diff.
"""

import tempfile
from pathlib import Path

import pytest

from saathi.identity.preferences import (
    LANGUAGE_KEY,
    PreferenceLocked,
    read_preference,
    write_preference,
)
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


def test_writing_the_same_key_twice_raises_preference_locked(store):
    write_preference(store, LANGUAGE_KEY, "chinese")
    with pytest.raises(PreferenceLocked) as exc_info:
        write_preference(store, LANGUAGE_KEY, "hindi")
    assert exc_info.value.key == LANGUAGE_KEY

    # The first write must still be intact -- a failed second write isn't
    # allowed to have partially clobbered anything.
    assert read_preference(store, LANGUAGE_KEY) == "chinese"


def test_different_keys_dont_interfere(store):
    write_preference(store, "language", "chinese")
    write_preference(store, "tts_backend", "piper")
    assert read_preference(store, "language") == "chinese"
    assert read_preference(store, "tts_backend") == "piper"
