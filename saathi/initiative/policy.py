"""Decides when to stay quiet — SPEC.md, verbatim: "`policy.py` needs a
reason to speak, not the absence of a reason to stay quiet." That
sentence is the whole design: `should_speak()` below defaults to *no*
and only returns yes when every signal it checks is a real, positive
confirmation — not when a signal is merely unknown or absent. An
unconfirmed presence is not "no reason to suppress," it is exactly the
missing reason to speak.

None of the signals this reads are wired to real sensors yet — no
presence detection, no "something else is happening" (television, a
call) detection exists anywhere in this codebase. `PolicyContext`'s
fields default to the values that make `should_speak()` suppress
everything (`presence=None`, i.e. unconfirmed) rather than to values
that would make it permissive by default — a policy gate that's
permissive until proven otherwise is not a gate.

**Two lanes** (SPEC.md, "Initiative" — the two-lane rule). The first
version of the restraint rules here put reminders under the same daily
cap as everything else, "whatever the score." The dry run flagged the
consequence — a medication reminder could be capped by three unrelated
social utterances earlier the same day — and that instruction was
withdrawn: it's the difference between a companion and a medical
liability. Now:

- **The reminder lane** (`kind="scheduled"`): a due, not-yet-acknowledged
  reminder fires. Never capped, never subject to cooldown, never expired
  by the social budget, and it doesn't count toward the cap either — a
  day with three due reminders shouldn't leave her silent otherwise.
  "Acknowledged" has no real signal yet (nothing speaks, nothing
  listens for a reply); today it means "already fired," which is what
  `scheduler.py`'s dedup already treats as resolved. Every due reminder
  in a tick fires — each is its own obligation, not a competitor for a
  slot.
- **The social lane** (everything else — "noticed", and event/ambient
  once those exist): expiry, then a daily cap of 3
  (`PolicyConfig.daily_cap`, counting *social* fires only), then a
  90-minute cooldown (`PolicyConfig.cooldown_minutes`), then one winner
  per tick by score (the reflected rule's own `confidence`,
  `identity/reflect.py`). Losers are logged "lost to a higher-scoring
  candidate this tick" and compete again next tick — not a queue, not
  dropped. Expiry (`PolicyConfig.noticed_expiry_days`) is the one
  terminal state besides firing: asking about Thursday's scan is kind
  on Friday and strange on Sunday. No date-parsing (that would need a
  model call, and the tick stays pure local queries) — just how long
  ago it was observed.
- **One crossover:** a reminder firing resets the social cooldown.
  Reminders don't spend the budget, but they do reset the clock — she
  shouldn't say "time for your tablets" and then chatter about the scan
  thirty seconds later. Mechanically: reminders are logged *before* the
  social lane is evaluated in the same tick, and the cooldown clock
  reads the latest fired row of *any* kind.

The social gate (`should_speak()` — presence, quiet hours, busy) is the
social lane's. The reminder lane has its own, `reminder_may_fire()`:
presence, yes — a reminder due while she's out is held until she's
back, not dropped — but *not* quiet hours. If someone deliberately set a
reminder for 10pm, 10pm is the point; quiet hours exist to stop
unsolicited chatter, not to withhold a scheduled dose. `busy` is kept
for reminders as a judgment call (DECISIONS.md): held one tick, never
dropped.

**Every candidate is still logged, allowed or not, with why** (SPEC.md:
"otherwise 'why did it say that' is undebuggable") — expired, gated,
capped, cooled down, or outscored, each a distinct, inspectable reason.
`spoken` is always `0`: this builds the decision machinery and makes it
inspectable; nothing in this codebase calls `say()` from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from saathi.identity.store import IdentityStore
from saathi.initiative.scheduler import EXPIRED_PREFIX, InitiativeCandidate

REMINDER_KIND = "scheduled"


@dataclass(frozen=True)
class PolicyContext:
    """Every field defaults to "no confirmed signal", which
    `should_speak()` treats as a reason to suppress, not a reason to
    proceed — see this module's docstring."""

    presence: bool | None = None  # True only if something has confirmed she's there
    quiet_hours: bool = False
    busy: bool = False  # television, a call, anything already happening


@dataclass(frozen=True)
class PolicyConfig:
    """The social lane's budget. None of it applies to reminders."""

    cooldown_minutes: int = 90
    daily_cap: int = 3
    noticed_expiry_days: int = 2


@dataclass(frozen=True)
class PolicyDecision:
    speak: bool
    reason: str  # the affirmative reason to speak, or the reason it was held back


def should_speak(candidate: InitiativeCandidate, context: PolicyContext) -> PolicyDecision:
    if context.presence is not True:
        return PolicyDecision(
            False, "presence not confirmed — no confirmed reason she's there to hear it"
        )
    if context.quiet_hours:
        return PolicyDecision(False, "quiet hours")
    if context.busy:
        return PolicyDecision(False, "something else is already happening")
    return PolicyDecision(True, candidate.reason)


def reminder_may_fire(candidate: InitiativeCandidate, context: PolicyContext) -> PolicyDecision:
    """The reminder lane's gate: presence, and nothing else from the
    social gate. Quiet hours exist to stop unsolicited chatter, not to
    withhold a dose someone deliberately scheduled for 10pm — 10pm is
    the point. Presence still holds (a reminder to an empty room helps
    no one; it returns next tick). `busy` is kept, as a judgment call
    recorded in DECISIONS.md: a reminder mid-phone-call waits one tick,
    it isn't dropped."""
    if context.presence is not True:
        return PolicyDecision(
            False, "presence not confirmed — no confirmed reason she's there to hear it"
        )
    if context.busy:
        return PolicyDecision(False, "something else is already happening")
    return PolicyDecision(True, candidate.reason)


def _score(candidate: InitiativeCandidate) -> float:
    # Social lane only -- reminders never compete for a slot. reflect.py's
    # own confidence is already a real, derived number (see that
    # module), not invented for this.
    return candidate.confidence if candidate.confidence is not None else 0.0


def _is_expired(
    candidate: InitiativeCandidate, store: IdentityStore, now: datetime, config: PolicyConfig
) -> tuple[bool, str]:
    if candidate.kind != "noticed" or candidate.source_episode is None:
        return False, ""
    rows = store.read("episodes", id=candidate.source_episode)
    if not rows:
        # Provenance is gone -- can't judge how stale this is, and a
        # "noticed" item with nothing to point back to is exactly the
        # kind of thing that shouldn't linger indefinitely on faith.
        return True, f"{EXPIRED_PREFIX} source episode no longer exists"
    episode_ts = datetime.fromisoformat(rows[0]["ts"])
    if episode_ts.tzinfo is None:
        episode_ts = episode_ts.replace(tzinfo=timezone.utc)
    age = now - episode_ts
    if age > timedelta(days=config.noticed_expiry_days):
        return (
            True,
            f"{EXPIRED_PREFIX} missed its useful window "
            f"({age.days} days since observed, limit {config.noticed_expiry_days})",
        )
    return False, ""


def _fired_rows(store: IdentityStore) -> list[dict]:
    return [row for row in store.read("initiatives") if row["suppressed_by"] is None]


def _row_ts(row: dict) -> datetime:
    ts = datetime.fromisoformat(row["ts"])
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _social_fired_today_count(store: IdentityStore, now: datetime) -> int:
    # Reminders don't spend the social budget -- excluded from the count.
    return sum(
        1
        for row in _fired_rows(store)
        if row["kind"] != REMINDER_KIND and _row_ts(row).date() == now.date()
    )


def _minutes_since_last_fired(store: IdentityStore, now: datetime) -> float | None:
    # Any kind -- a reminder firing resets the social clock (the one
    # crossover between the lanes).
    fired = _fired_rows(store)
    if not fired:
        return None
    last_fired_at = max(_row_ts(row) for row in fired)
    return (now - last_fired_at).total_seconds() / 60


def evaluate(
    store: IdentityStore,
    candidates: list[InitiativeCandidate],
    context: PolicyContext,
    *,
    now: datetime | None = None,
    config: PolicyConfig | None = None,
) -> list[dict]:
    """Runs both lanes over every candidate and logs each one to
    `initiatives`, allowed, held back, or expired, then returns the rows
    actually written — the thing to read to see what this tick decided,
    without a separate query."""
    now = now or datetime.now(timezone.utc)
    config = config or PolicyConfig()
    written: list[dict] = []

    def _log(candidate: InitiativeCandidate, suppressed_by: str | None) -> None:
        row_id = store.append(
            "initiatives",
            ts=now.isoformat(),
            kind=candidate.kind,
            reason=candidate.reason,
            source_episode=candidate.source_episode,
            spoken=0,
            suppressed_by=suppressed_by,
        )
        written.extend(store.read("initiatives", id=row_id))

    reminders = [c for c in candidates if c.kind == REMINDER_KIND]
    social = [c for c in candidates if c.kind != REMINDER_KIND]

    # -- Reminder lane: due and not yet acknowledged -> fires, through
    # its own gate (presence, not quiet hours). Logged first, so the
    # social lane's cooldown clock below sees it.
    for candidate in reminders:
        decision = reminder_may_fire(candidate, context)
        _log(candidate, None if decision.speak else decision.reason)

    # -- Social lane.
    # 1. Expiry -- terminal, checked before anything else competes.
    survivors = []
    for candidate in social:
        expired, reason = _is_expired(candidate, store, now, config)
        if expired:
            _log(candidate, reason)
        else:
            survivors.append(candidate)

    # 2. The base gate -- non-terminal.
    gated = []
    for candidate in survivors:
        decision = should_speak(candidate, context)
        if decision.speak:
            gated.append(candidate)
        else:
            _log(candidate, decision.reason)

    # 3. Daily cap -- social fires only; reminders neither count nor block.
    if _social_fired_today_count(store, now) >= config.daily_cap:
        for candidate in gated:
            _log(candidate, f"daily cap reached ({config.daily_cap}/day)")
        return written

    # 4. Cooldown -- since the last fired utterance of any kind,
    #    including a reminder fired moments ago in this same tick.
    minutes_since_last_fired = _minutes_since_last_fired(store, now)
    eligible = []
    for candidate in gated:
        if (
            minutes_since_last_fired is not None
            and minutes_since_last_fired < config.cooldown_minutes
        ):
            _log(
                candidate,
                f"cooldown ({config.cooldown_minutes} min between utterances; "
                f"{minutes_since_last_fired:.0f} min since the last one)",
            )
        else:
            eligible.append(candidate)

    if not eligible:
        return written

    # 5. One winner, by score; the rest stay candidates, not a queue.
    winner = max(eligible, key=_score)
    for candidate in eligible:
        if candidate is winner:
            _log(candidate, None)
        else:
            _log(candidate, "lost to a higher-scoring candidate this tick")

    return written
