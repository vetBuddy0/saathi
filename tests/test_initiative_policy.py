"""initiative/policy.py — "a reason to speak, not the absence of a
reason to stay quiet" (SPEC.md), under direct test: the default context
(no confirmed signals) must suppress, not permit.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from saathi.identity.store import IdentityStore
from saathi.initiative.policy import PolicyContext, evaluate, should_speak
from saathi.initiative.scheduler import InitiativeCandidate

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
CANDIDATE = InitiativeCandidate(kind="noticed", reason="she has a scan Thursday")


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def test_default_context_suppresses_even_with_no_other_signal():
    # The whole point: PolicyContext() with nothing set must not default
    # to "sure, go ahead" just because nothing said no.
    decision = should_speak(CANDIDATE, PolicyContext())
    assert decision.speak is False
    assert "presence" in decision.reason


def test_confirmed_presence_and_nothing_else_going_on_allows_speech():
    decision = should_speak(CANDIDATE, PolicyContext(presence=True))
    assert decision.speak is True
    assert decision.reason == CANDIDATE.reason


def test_quiet_hours_suppresses_even_with_confirmed_presence():
    decision = should_speak(CANDIDATE, PolicyContext(presence=True, quiet_hours=True))
    assert decision.speak is False
    assert "quiet hours" in decision.reason


def test_busy_suppresses_even_with_confirmed_presence():
    decision = should_speak(CANDIDATE, PolicyContext(presence=True, busy=True))
    assert decision.speak is False
    assert "already happening" in decision.reason


def test_presence_false_is_not_the_same_as_unknown_and_still_suppresses():
    decision = should_speak(CANDIDATE, PolicyContext(presence=False))
    assert decision.speak is False


def test_evaluate_logs_an_allowed_candidate_with_no_suppression_reason_and_unspoken(store):
    rows = evaluate(store, [CANDIDATE], PolicyContext(presence=True), now=NOW)
    assert len(rows) == 1
    assert rows[0]["suppressed_by"] is None
    assert rows[0]["spoken"] == 0  # never speaks unprompted in this pass, allowed or not
    assert rows[0]["kind"] == "noticed"
    assert rows[0]["reason"] == CANDIDATE.reason


def test_evaluate_logs_a_suppressed_candidate_with_its_reason(store):
    rows = evaluate(store, [CANDIDATE], PolicyContext(), now=NOW)
    assert len(rows) == 1
    assert rows[0]["suppressed_by"] is not None
    assert rows[0]["spoken"] == 0


def test_evaluate_logs_every_candidate_even_a_mix_of_allowed_and_suppressed(store):
    # A "scheduled" candidate always outscores a "noticed" one (see
    # test_initiative_policy_rules.py's dedicated scoring tests), so of
    # this pair only the reminder fires this tick -- the other is
    # logged too, not dropped, just not the winner.
    allowed = InitiativeCandidate(kind="scheduled", reason="a reminder is due")
    rows = evaluate(store, [allowed, CANDIDATE], PolicyContext(presence=True), now=NOW)
    assert len(rows) == 2
    fired = [r for r in rows if r["suppressed_by"] is None]
    assert len(fired) == 1
    assert fired[0]["kind"] == "scheduled"

    rows_suppressed = evaluate(store, [allowed], PolicyContext(busy=True), now=NOW)
    assert rows_suppressed[0]["suppressed_by"] is not None


def test_evaluate_actually_persists_to_the_store_not_just_returns(store):
    evaluate(store, [CANDIDATE], PolicyContext(presence=True), now=NOW)
    assert len(store.read("initiatives")) == 1
