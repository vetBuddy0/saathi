"""identity/usage.py -- per-voice spend folded from real `turns` rows.
Rows before the picker (voice NULL) are not attributed; partial rows
count the turn and add nothing to the sums.
"""

import tempfile
from pathlib import Path

import pytest

from saathi.identity.store import IdentityStore
from saathi.identity.usage import TTSUsage, tts_usage


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def _turn(store, voice=None, tts_chars=None, tts_cost_usd=None):
    store.append(
        "turns",
        ts="2026-09-25T00:00:00+00:00",
        mode="voice",
        eou_ms=500,
        handoff=0,
        engine="cascade",
        voice=voice,
        tts_chars=tts_chars,
        tts_cost_usd=tts_cost_usd,
    )


def test_empty_store_has_no_usage(store):
    assert tts_usage(store) == {}


def test_sums_turns_chars_and_cost_per_voice(store):
    _turn(store, "warm", 100, 0.003)
    _turn(store, "warm", 50, 0.0015)
    _turn(store, "plain", 80, 0.0)
    usage = tts_usage(store)
    assert usage["warm"] == TTSUsage(turns=2, chars=150, cost_usd=pytest.approx(0.0045))
    assert usage["plain"] == TTSUsage(turns=1, chars=80, cost_usd=0.0)


def test_rows_before_the_picker_are_not_attributed_to_anyone(store):
    _turn(store)  # voice NULL: a turn logged before the columns existed
    _turn(store, "warm", 10, 0.0003)
    usage = tts_usage(store)
    assert set(usage) == {"warm"}
    assert usage["warm"].turns == 1


def test_a_turn_with_a_voice_but_no_numbers_counts_the_turn_only(store):
    _turn(store, "soft")
    assert tts_usage(store)["soft"] == TTSUsage(turns=1, chars=0, cost_usd=0.0)
