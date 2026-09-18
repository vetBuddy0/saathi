"""saathi/latency_budget.py — SPEC.md's "A test reads the turns table
and fails the build when a turn exceeds budget," made concrete. Runs
against a real (temp-file) IdentityStore with synthetic rows, since
there's no real device in CI to read turns from (see that module's
docstring) — what CI *can* gate on is that this percentile math and
budget comparison behave correctly, which is what these tests prove.
"""

import tempfile
from pathlib import Path

import pytest

from saathi.identity.store import IdentityStore
from saathi.latency_budget import (
    BRAIN_FINISHES_BUDGET_MS,
    VOICE_STARTS_BUDGET_MS,
    check_latency_budget,
    percentile_95,
)


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def _log_turn(
    store,
    *,
    stt_ms=None,
    first_token_ms=None,
    first_tts_chunk_ms=None,
    engine="cascade",
    eou_ms=500,
):
    store.append(
        "turns",
        ts="2026-09-18T00:00:00+00:00",
        mode="voice",
        eou_ms=eou_ms,
        stt_ms=stt_ms,
        first_token_ms=first_token_ms,
        first_tts_chunk_ms=first_tts_chunk_ms,
        prompt_tokens=None,
        completion_tokens=None,
        cost_usd=None,
        handoff=0,
        engine=engine,
    )


def test_percentile_95_of_100_values_is_the_95th_smallest():
    values = list(range(1, 101))  # 1..100
    assert percentile_95(values) == 95


def test_percentile_95_of_a_single_value_is_that_value():
    assert percentile_95([42]) == 42


def test_percentile_95_of_empty_list_raises():
    with pytest.raises(ValueError):
        percentile_95([])


def test_empty_turns_table_passes_with_a_note(store):
    result = check_latency_budget(store, engine="cascade")
    assert result.passed is True
    assert result.turns_checked == 0
    assert "no turns logged" in result.note


def test_all_turns_within_budget_passes(store):
    for _ in range(20):
        # brain (stt+first_token) = 800, voice (+ first_tts_chunk) = 900
        _log_turn(store, stt_ms=400, first_token_ms=400, first_tts_chunk_ms=100)

    result = check_latency_budget(store, engine="cascade")
    assert result.passed is True
    assert result.turns_checked == 20
    assert result.voice_starts_p95_ms == 900
    assert result.brain_finishes_p95_ms == 800


def test_a_95th_percentile_over_budget_fails_even_with_a_good_mean(store):
    # 18 fast turns, two very slow ones -- the mean would look fine
    # (2/20 = 10% bad turns barely nudges it), the 95th percentile (the
    # whole point of SPEC.md's instruction) must not. Nearest-rank p95 of
    # 20 values is the 19th-smallest (index 18) -- with only a *single*
    # outlier that rank would still land on a fast turn, so this needs at
    # least two slow turns to actually cross the threshold; that's the
    # real, verified shape of the 95th-percentile calculation, not an
    # arbitrary test choice.
    for _ in range(18):
        _log_turn(store, stt_ms=250, first_token_ms=250, first_tts_chunk_ms=100)  # voice=600
    for _ in range(2):
        _log_turn(store, stt_ms=250, first_token_ms=250, first_tts_chunk_ms=4500)  # voice=5000

    result = check_latency_budget(store, engine="cascade")
    assert result.passed is False
    assert result.voice_starts_p95_ms == 5000


def test_voice_starts_can_fail_while_brain_finishes_stays_within_budget(store):
    # voice_starts is stt_ms + first_token_ms + first_tts_chunk_ms, so it
    # can never be *less* than brain_finishes (stt_ms + first_token_ms) --
    # a slow TTS stage alone can still push voice_starts over budget
    # while the STT+LLM portion stays comfortably under 3000ms, proving
    # the two budgets are genuinely checked independently rather than
    # one masking the other.
    for _ in range(20):
        _log_turn(store, stt_ms=100, first_token_ms=100, first_tts_chunk_ms=2000)

    result = check_latency_budget(store, engine="cascade")
    assert result.brain_finishes_p95_ms == 200
    assert result.brain_finishes_p95_ms <= BRAIN_FINISHES_BUDGET_MS
    assert result.voice_starts_p95_ms == 2200
    assert result.voice_starts_p95_ms > VOICE_STARTS_BUDGET_MS["cascade"]
    assert result.passed is False


def test_brain_finishes_over_budget_fails_even_when_voice_starts_would_too(store):
    for _ in range(20):
        # stt+first_token alone already exceeds the 3000ms brain budget
        _log_turn(store, stt_ms=3000, first_token_ms=500, first_tts_chunk_ms=50)

    result = check_latency_budget(store, engine="cascade")
    assert result.brain_finishes_p95_ms == 3500
    assert result.brain_finishes_p95_ms > BRAIN_FINISHES_BUDGET_MS
    assert result.passed is False


def test_realtime_engine_uses_the_tighter_500ms_budget(store):
    for _ in range(20):
        _log_turn(store, stt_ms=50, first_token_ms=50, first_tts_chunk_ms=700, engine="realtime")

    result = check_latency_budget(store, engine="realtime")
    assert result.voice_starts_budget_ms == VOICE_STARTS_BUDGET_MS["realtime"]
    assert result.voice_starts_p95_ms == 800
    assert result.passed is False  # 800ms exceeds realtime's 500ms budget
    # The same data would pass under cascade's looser 1200ms budget --
    # proving the two engines really are checked against different numbers.
    _log_turn(store, stt_ms=50, first_token_ms=50, first_tts_chunk_ms=700, engine="cascade")
    cascade_result = check_latency_budget(store, engine="cascade")
    assert cascade_result.passed is True


def test_rows_missing_granular_timings_are_excluded_not_treated_as_zero(store):
    _log_turn(store)  # eou-only row, everything else None
    _log_turn(store, stt_ms=100, first_token_ms=100, first_tts_chunk_ms=100)  # voice=300

    result = check_latency_budget(store, engine="cascade")
    assert result.turns_checked == 2
    assert result.voice_starts_p95_ms == 300  # the all-None row didn't drag this toward 0
    assert result.passed is True


def test_a_row_missing_only_first_tts_chunk_ms_is_excluded_from_voice_starts_but_not_brain_finishes(
    store,
):
    _log_turn(store, stt_ms=100, first_token_ms=100, first_tts_chunk_ms=None)
    result = check_latency_budget(store, engine="cascade")
    assert result.voice_starts_p95_ms is None  # can't sum a missing component
    assert result.brain_finishes_p95_ms == 200  # doesn't need first_tts_chunk_ms at all


def test_a_different_engines_turns_dont_affect_this_engines_budget_check(store):
    _log_turn(store, stt_ms=2500, first_token_ms=2500, first_tts_chunk_ms=0, engine="realtime")
    _log_turn(store, stt_ms=100, first_token_ms=100, first_tts_chunk_ms=100, engine="cascade")

    result = check_latency_budget(store, engine="cascade")
    assert result.turns_checked == 1
    assert result.voice_starts_p95_ms == 300
    assert result.passed is True
