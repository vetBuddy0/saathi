"""The scheduler tick is pure local queries -- never a model call. The
model is only ever involved once policy has already decided something
is worth saying (turning an allowed candidate's reason into words to
actually speak, a later step this codebase doesn't build yet -- see
docs/initiative-dry-run.md for where that boundary sits).

Enforced two ways: source inspection (neither module even mentions
`groq`, so there's no path to a model call, not just "doesn't happen to
call one today") and a live check that a full propose+evaluate pass
completes correctly with `groq` itself made to explode if touched.
"""

import inspect
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from saathi.identity.store import IdentityStore
from saathi.initiative import policy, scheduler

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def test_scheduler_module_never_mentions_groq():
    source = inspect.getsource(scheduler)
    assert "groq" not in source.lower()


def test_policy_module_never_mentions_groq():
    source = inspect.getsource(policy)
    assert "groq" not in source.lower()


def test_a_full_pass_works_with_groq_itself_poisoned(monkeypatch):
    import groq

    def _explode(*args, **kwargs):
        raise AssertionError("scheduler/policy touched Groq -- the tick must be pure local queries")

    monkeypatch.setattr(groq, "Groq", _explode)

    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        store.append(
            "reminders",
            due_at=(NOW - timedelta(minutes=5)).isoformat(),
            text="take your morning tablets",
            recurrence=None,
            active=1,
        )
        episode_id = store.append(
            "episodes", ts=NOW.isoformat(), entity_id=None, text="e", importance=5.0,
            embedding=None,
        )
        store.append(
            "rules", text="a confident, active rule", confidence=0.9, learned_at=NOW.isoformat(),
            source_episode=episode_id, active=1,
        )

        candidates = scheduler.propose_candidates(store, now=NOW)
        assert len(candidates) == 2  # one scheduled, one noticed -- both real, both without Groq
        decisions = policy.evaluate(store, candidates, policy.PolicyContext(presence=True), now=NOW)
        assert len(decisions) == 2
