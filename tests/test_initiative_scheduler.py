"""initiative/scheduler.py — the three candidate sources. Never decides
whether to speak (that's policy.py) -- these tests only check what gets
proposed and how deduplication works against real IdentityStore data.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from saathi.identity.store import IdentityStore
from saathi.initiative.scheduler import (
    event_candidates,
    noticed_candidates,
    propose_candidates,
    scheduled_candidates,
)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


# -- scheduled --------------------------------------------------------


def test_scheduled_candidates_empty_store_is_empty(store):
    assert scheduled_candidates(store, now=NOW) == []


def test_scheduled_candidates_includes_a_due_reminder(store):
    store.append(
        "reminders",
        due_at=(NOW - timedelta(minutes=5)).isoformat(),
        text="take your morning tablets",
        recurrence=None,
        active=1,
    )
    candidates = scheduled_candidates(store, now=NOW)
    assert len(candidates) == 1
    assert candidates[0].kind == "scheduled"
    assert "take your morning tablets" in candidates[0].reason


def test_scheduled_candidates_excludes_a_reminder_not_yet_due(store):
    store.append(
        "reminders",
        due_at=(NOW + timedelta(hours=1)).isoformat(),
        text="future reminder",
        recurrence=None,
        active=1,
    )
    assert scheduled_candidates(store, now=NOW) == []


def test_scheduled_candidates_excludes_an_inactive_reminder(store):
    store.append(
        "reminders",
        due_at=(NOW - timedelta(minutes=5)).isoformat(),
        text="cancelled reminder",
        recurrence=None,
        active=0,
    )
    assert scheduled_candidates(store, now=NOW) == []


def test_scheduled_candidates_deduplicates_against_already_proposed_reminders(store):
    reminder_id = store.append(
        "reminders",
        due_at=(NOW - timedelta(minutes=5)).isoformat(),
        text="take your morning tablets",
        recurrence=None,
        active=1,
    )
    # Simulate a previous pass already having proposed this one.
    store.append(
        "initiatives",
        ts=NOW.isoformat(),
        kind="scheduled",
        reason=f"Reminder due: take your morning tablets [reminder_id={reminder_id}]",
        source_episode=None,
        spoken=0,
        suppressed_by=None,
    )
    assert scheduled_candidates(store, now=NOW) == []


# -- event --------------------------------------------------------------


def test_event_candidates_is_always_empty_today(store):
    assert event_candidates(store) == []


# -- noticed --------------------------------------------------------------


def test_noticed_candidates_empty_store_is_empty(store):
    assert noticed_candidates(store) == []


def test_noticed_candidates_includes_a_confident_active_rule(store):
    episode_id = store.append(
        "episodes", ts=NOW.isoformat(), entity_id=None, text="e", importance=5.0, embedding=None
    )
    store.append(
        "rules",
        text="She has a scan Thursday and seems anxious.",
        confidence=0.8,
        learned_at=NOW.isoformat(),
        source_episode=episode_id,
        active=1,
    )
    candidates = noticed_candidates(store)
    assert len(candidates) == 1
    assert candidates[0].kind == "noticed"
    assert candidates[0].source_episode == episode_id


def test_noticed_candidates_excludes_low_confidence_rules(store):
    episode_id = store.append(
        "episodes", ts=NOW.isoformat(), entity_id=None, text="e", importance=5.0, embedding=None
    )
    store.append(
        "rules",
        text="a barely-grounded guess",
        confidence=0.2,
        learned_at=NOW.isoformat(),
        source_episode=episode_id,
        active=1,
    )
    assert noticed_candidates(store) == []


def test_noticed_candidates_excludes_inactive_rules(store):
    episode_id = store.append(
        "episodes", ts=NOW.isoformat(), entity_id=None, text="e", importance=5.0, embedding=None
    )
    store.append(
        "rules",
        text="a retracted rule",
        confidence=0.9,
        learned_at=NOW.isoformat(),
        source_episode=episode_id,
        active=0,
    )
    assert noticed_candidates(store) == []


def test_noticed_candidates_deduplicates_against_already_proposed_episodes(store):
    episode_id = store.append(
        "episodes", ts=NOW.isoformat(), entity_id=None, text="e", importance=5.0, embedding=None
    )
    store.append(
        "rules",
        text="already noticed once",
        confidence=0.9,
        learned_at=NOW.isoformat(),
        source_episode=episode_id,
        active=1,
    )
    store.append(
        "initiatives",
        ts=NOW.isoformat(),
        kind="noticed",
        reason="already noticed once",
        source_episode=episode_id,
        spoken=0,
        suppressed_by=None,
    )
    assert noticed_candidates(store) == []


def test_two_rules_from_the_same_episode_are_two_separate_candidates(store):
    # Dedup is on the rule, not the source episode: one firing must not
    # resolve the other. Two things noticed about one observation are
    # two things to say.
    episode_id = store.append(
        "episodes", ts=NOW.isoformat(), entity_id=None, text="e", importance=5.0, embedding=None
    )
    for text in ("first insight", "second insight"):
        store.append(
            "rules", text=text, confidence=0.9, learned_at=NOW.isoformat(),
            source_episode=episode_id, active=1,
        )
    assert {c.reason for c in noticed_candidates(store)} == {"first insight", "second insight"}

    # The first fires; the second must still be proposed.
    store.append(
        "initiatives", ts=NOW.isoformat(), kind="noticed", reason="first insight",
        source_episode=episode_id, spoken=0, suppressed_by=None,
    )
    assert [c.reason for c in noticed_candidates(store)] == ["second insight"]


# -- propose_candidates --------------------------------------------------


def test_propose_candidates_combines_all_three_sources(store):
    store.append(
        "reminders",
        due_at=(NOW - timedelta(minutes=5)).isoformat(),
        text="a due reminder",
        recurrence=None,
        active=1,
    )
    episode_id = store.append(
        "episodes", ts=NOW.isoformat(), entity_id=None, text="e", importance=5.0, embedding=None
    )
    store.append(
        "rules",
        text="a noticed insight",
        confidence=0.9,
        learned_at=NOW.isoformat(),
        source_episode=episode_id,
        active=1,
    )
    kinds = {c.kind for c in propose_candidates(store, now=NOW)}
    assert kinds == {"scheduled", "noticed"}
