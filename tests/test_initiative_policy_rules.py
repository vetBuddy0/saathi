"""The four rules that fixed the initiative-clustering bug the dry run
first exposed: one utterance per tick (highest score wins, the rest
stay candidates), cooldown (reminders exempt), a daily cap ("whatever
the score"), and expiry for stale "noticed" candidates. Each tested
directly against real `IdentityStore` data, not mocked.

`initiatives.source_episode` is a real foreign key into `episodes` --
every candidate below that isn't `source_episode=None` needs a real
episode row first (`_episode()`), not an arbitrary int.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from saathi.identity.store import IdentityStore
from saathi.initiative.policy import PolicyConfig, PolicyContext, evaluate
from saathi.initiative.scheduler import InitiativeCandidate, noticed_candidates

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
PRESENT = PolicyContext(presence=True)


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def _episode(store, hours_ago=1, importance=5.0):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return store.append(
        "episodes", ts=ts, entity_id=None, text="e", importance=importance, embedding=None
    )


def _rule(store, episode_id, confidence, text="a noticed rule"):
    store.append(
        "rules", text=text, confidence=confidence, learned_at=NOW.isoformat(),
        source_episode=episode_id, active=1,
    )


def _noticed(store, reason, confidence, hours_ago=1):
    episode_id = _episode(store, hours_ago)
    return InitiativeCandidate(
        kind="noticed", reason=reason, source_episode=episode_id, confidence=confidence
    )


# -- one per tick, highest score wins ------------------------------------


def test_only_the_highest_confidence_noticed_candidate_fires(store):
    low = _noticed(store, "low", 0.5)
    high = _noticed(store, "high", 0.9)
    rows = evaluate(store, [low, high], PRESENT, now=NOW)
    fired = [r for r in rows if r["suppressed_by"] is None]
    assert len(fired) == 1
    assert fired[0]["reason"] == "high"
    lost = [r for r in rows if r["reason"] == "low"][0]
    assert lost["suppressed_by"] == "lost to a higher-scoring candidate this tick"


def test_a_reminder_and_a_noticed_candidate_in_one_tick_only_the_reminder_fires(store):
    # The crossover: the reminder fires (its own lane), and firing it
    # resets the social cooldown -- so the noticed candidate is held
    # back this tick by cooldown, not by losing a score competition.
    # "Time for your tablets" then chatter about the scan thirty seconds
    # later is exactly what this prevents.
    reminder = InitiativeCandidate(kind="scheduled", reason="reminder")
    noticed = _noticed(store, "noticed", 1.0)
    rows = evaluate(store, [noticed, reminder], PRESENT, now=NOW)
    fired = [r for r in rows if r["suppressed_by"] is None]
    assert len(fired) == 1
    assert fired[0]["kind"] == "scheduled"
    held = [r for r in rows if r["kind"] == "noticed"][0]
    assert "cooldown" in held["suppressed_by"]


def test_losing_candidates_are_not_a_queue_they_compete_fresh_next_tick(store):
    low = _noticed(store, "low", 0.5)
    high = _noticed(store, "high", 0.9)
    evaluate(store, [low, high], PRESENT, now=NOW)  # "high" fires, "low" loses

    later = NOW + timedelta(hours=3)
    # Next tick: "high" no longer exists as a candidate (it already
    # fired), but "low" is proposed again and, alone, wins on its own
    # merits.
    rows = evaluate(store, [low], PRESENT, now=later)
    fired = [r for r in rows if r["suppressed_by"] is None]
    assert len(fired) == 1
    assert fired[0]["reason"] == "low"


# -- cooldown -------------------------------------------------------------


def test_a_second_noticed_candidate_within_the_cooldown_window_is_held_back(store):
    first = _noticed(store, "first", 0.9)
    evaluate(store, [first], PRESENT, now=NOW)

    second = _noticed(store, "second", 0.9)
    soon = NOW + timedelta(minutes=30)  # well under the 90-minute default
    rows = evaluate(store, [second], PRESENT, now=soon)
    assert rows[0]["suppressed_by"] is not None
    assert "cooldown" in rows[0]["suppressed_by"]


def test_a_candidate_after_the_cooldown_window_fires(store):
    first = _noticed(store, "first", 0.9)
    evaluate(store, [first], PRESENT, now=NOW)

    second = _noticed(store, "second", 0.9)
    later = NOW + timedelta(minutes=91)
    rows = evaluate(store, [second], PRESENT, now=later)
    assert rows[0]["suppressed_by"] is None


def test_a_due_reminder_is_exempt_from_cooldown(store):
    first = _noticed(store, "first", 0.9)
    evaluate(store, [first], PRESENT, now=NOW)

    reminder = InitiativeCandidate(kind="scheduled", reason="take your tablet")
    soon = NOW + timedelta(minutes=5)  # nowhere near the cooldown window
    rows = evaluate(store, [reminder], PRESENT, now=soon)
    assert rows[0]["suppressed_by"] is None


def test_cooldown_is_configurable(store):
    first = _noticed(store, "first", 0.9)
    evaluate(store, [first], PRESENT, now=NOW)

    second = _noticed(store, "second", 0.9)
    soon = NOW + timedelta(minutes=10)
    rows = evaluate(
        store, [second], PRESENT, now=soon, config=PolicyConfig(cooldown_minutes=5)
    )
    assert rows[0]["suppressed_by"] is None


# -- daily cap -------------------------------------------------------------


def test_the_default_daily_cap_is_three(store):
    for i in range(3):
        candidate = _noticed(store, f"n{i}", 0.9)
        # Space them out past the cooldown so cooldown isn't what blocks them.
        evaluate(store, [candidate], PRESENT, now=NOW + timedelta(hours=2 * i))

    fourth = _noticed(store, "fourth", 0.9)
    rows = evaluate(store, [fourth], PRESENT, now=NOW + timedelta(hours=10))
    assert rows[0]["suppressed_by"] is not None
    assert "daily cap" in rows[0]["suppressed_by"]


# -- the two lanes ----------------------------------------------------------


def _exhaust_social_cap(store):
    for i in range(3):
        candidate = _noticed(store, f"n{i}", 0.9)
        rows = evaluate(store, [candidate], PRESENT, now=NOW + timedelta(hours=2 * i))
        assert rows[0]["suppressed_by"] is None  # all three really fired


def test_a_reminder_fires_on_a_day_where_the_social_cap_is_exhausted(store):
    # The one that matters -- the difference between a companion and a
    # medical liability. Three social utterances have used up the day's
    # cap; a due reminder must still fire.
    _exhaust_social_cap(store)

    reminder = InitiativeCandidate(kind="scheduled", reason="take your tablet")
    rows = evaluate(store, [reminder], PRESENT, now=NOW + timedelta(hours=10))
    assert rows[0]["suppressed_by"] is None

    # And the cap still holds for the social lane on the same day
    # (NOW is 12:00 -- +11h is still today; +12h would be midnight).
    social = _noticed(store, "one more", 0.99)
    rows = evaluate(store, [social], PRESENT, now=NOW + timedelta(hours=11))
    assert "daily cap" in rows[0]["suppressed_by"]


def test_reminders_do_not_count_toward_the_social_cap(store):
    for i in range(3):
        reminder = InitiativeCandidate(kind="scheduled", reason=f"reminder {i}")
        rows = evaluate(store, [reminder], PRESENT, now=NOW + timedelta(hours=i))
        assert rows[0]["suppressed_by"] is None

    # Three reminders fired today; the social lane's budget is untouched.
    social = _noticed(store, "a noticed thing", 0.9)
    rows = evaluate(store, [social], PRESENT, now=NOW + timedelta(hours=5))
    assert rows[0]["suppressed_by"] is None


def test_every_due_reminder_in_a_tick_fires_not_just_one(store):
    # Each reminder is its own obligation, not a competitor for a slot.
    morning = InitiativeCandidate(kind="scheduled", reason="blood pressure tablet")
    other = InitiativeCandidate(kind="scheduled", reason="eye drops")
    rows = evaluate(store, [morning, other], PRESENT, now=NOW)
    assert [r["suppressed_by"] for r in rows] == [None, None]


def test_a_reminder_firing_resets_the_social_cooldown(store):
    first = _noticed(store, "first", 0.9)
    evaluate(store, [first], PRESENT, now=NOW)

    reminder = InitiativeCandidate(kind="scheduled", reason="take your tablet")
    rows = evaluate(store, [reminder], PRESENT, now=NOW + timedelta(minutes=100))
    assert rows[0]["suppressed_by"] is None  # past the first's cooldown, and exempt anyway

    # 100 min after the first noticed, but only 60 min after the
    # reminder: the reminder reset the clock.
    second = _noticed(store, "second", 0.9)
    rows = evaluate(store, [second], PRESENT, now=NOW + timedelta(minutes=160))
    assert "cooldown" in rows[0]["suppressed_by"]
    assert "60 min since the last one" in rows[0]["suppressed_by"]

    rows = evaluate(store, [second], PRESENT, now=NOW + timedelta(minutes=191))
    assert rows[0]["suppressed_by"] is None


def test_a_reminder_is_still_held_when_presence_is_unconfirmed_but_not_dropped(store):
    # The base gate applies to both lanes (a reminder to an empty room
    # helps no one) -- non-terminal, so it fires once she's back.
    reminder = InitiativeCandidate(kind="scheduled", reason="take your tablet")
    rows = evaluate(store, [reminder], PolicyContext(), now=NOW)
    assert "presence" in rows[0]["suppressed_by"]

    rows = evaluate(store, [reminder], PRESENT, now=NOW + timedelta(minutes=10))
    assert rows[0]["suppressed_by"] is None


def test_the_daily_cap_resets_the_next_day(store):
    for i in range(3):
        candidate = _noticed(store, f"n{i}", 0.9)
        evaluate(store, [candidate], PRESENT, now=NOW + timedelta(hours=2 * i))

    next_day = _noticed(store, "next day", 0.9)
    tomorrow = NOW + timedelta(days=1)
    rows = evaluate(store, [next_day], PRESENT, now=tomorrow)
    assert rows[0]["suppressed_by"] is None


def test_daily_cap_is_configurable(store):
    first = _noticed(store, "n0", 0.9)
    evaluate(store, [first], PRESENT, now=NOW)

    second = _noticed(store, "n1", 0.9)
    rows = evaluate(
        store, [second], PRESENT, now=NOW + timedelta(hours=2), config=PolicyConfig(daily_cap=1)
    )
    assert rows[0]["suppressed_by"] is not None
    assert "daily cap" in rows[0]["suppressed_by"]


# -- expiry ----------------------------------------------------------------


def test_a_fresh_noticed_candidate_does_not_expire(store):
    candidate = _noticed(store, "fresh", 0.9, hours_ago=1)
    rows = evaluate(store, [candidate], PRESENT, now=NOW)
    assert rows[0]["suppressed_by"] is None


def test_a_noticed_candidate_past_the_expiry_window_is_dropped(store):
    candidate = _noticed(store, "stale", 0.9, hours_ago=24 * 3)  # default limit is 2 days
    rows = evaluate(store, [candidate], PRESENT, now=NOW)
    assert rows[0]["suppressed_by"] is not None
    assert rows[0]["suppressed_by"].startswith("expired:")


def test_expiry_is_configurable(store):
    candidate = _noticed(store, "stale", 0.9, hours_ago=24 * 3)
    rows = evaluate(
        store, [candidate], PRESENT, now=NOW, config=PolicyConfig(noticed_expiry_days=7)
    )
    assert rows[0]["suppressed_by"] is None


def test_an_expired_candidate_is_never_reproposed_by_the_scheduler(store):
    episode_id = _episode(store, hours_ago=24 * 3)
    _rule(store, episode_id, confidence=0.9, text="a stale rule")
    candidate = InitiativeCandidate(
        kind="noticed", reason="a stale rule", source_episode=episode_id, confidence=0.9
    )
    evaluate(store, [candidate], PRESENT, now=NOW)  # expires it

    # scheduler.py must not propose it again -- expiry is terminal.
    assert noticed_candidates(store) == []


def test_a_reminder_candidate_never_expires_regardless_of_age(store):
    # Expiry as designed only applies to "noticed" -- a very overdue
    # reminder should still fire when it's finally heard, not be
    # silently dropped for being late.
    old_reminder = InitiativeCandidate(kind="scheduled", reason="very overdue reminder")
    rows = evaluate(store, [old_reminder], PRESENT, now=NOW)
    assert rows[0]["suppressed_by"] is None


def test_lost_and_capped_and_cooled_down_candidates_are_still_reproposed(store):
    # Non-terminal suppression reasons (lost to score, capped, cooled
    # down, or the base gate) must NOT stop scheduler.py from proposing
    # the same source_episode again -- only firing or expiry does.
    episode_id = _episode(store, hours_ago=1)
    _rule(store, episode_id, confidence=0.6, text="a losing rule")
    winner = _noticed(store, "winner", 0.99)
    loser = InitiativeCandidate(
        kind="noticed", reason="a losing rule", source_episode=episode_id, confidence=0.6
    )
    evaluate(store, [winner, loser], PRESENT, now=NOW)  # loser is logged as "lost", not resolved

    assert len(noticed_candidates(store)) == 1
    assert noticed_candidates(store)[0].reason == "a losing rule"
