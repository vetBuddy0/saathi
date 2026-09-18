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

**One utterance per tick, four rules deep** — found by the initiative
dry run (`docs/initiative-dry-run.md`, first version): reflecting once
over a seeded week produced five "noticed" candidates in a single tick,
all allowed at once, which is exactly the "device that comments on
everything" failure SPEC.md warns about, just concentrated into one
moment instead of spread across a session. `evaluate()` now runs, in
order:

1. **Expiry.** A "noticed" candidate whose source episode is more than
   `PolicyConfig.noticed_expiry_days` old is dropped — `suppressed_by`
   starts with `"expired:"`, which is the one *terminal* state besides
   actually firing (see `scheduler.py`'s dedup, which treats both as
   resolved and everything else as retry-next-tick). Asking about
   Thursday's scan is kind on Friday and strange on Sunday; there's no
   date-parsing here (that would need a model call, and the tick stays
   pure local queries) — just how long ago it was *observed*, which is
   the only thing available without one.
2. **The base gate** (`should_speak()`, unchanged) — presence, quiet
   hours, busy. Non-terminal: a candidate held back here is reconsidered
   next tick, not dropped.
3. **Daily cap** (`PolicyConfig.daily_cap`, default 3). Once today's
   count of *fired* initiatives reaches it, nothing more fires today,
   "whatever the score" — deliberately, this includes reminders; there
   is no exemption for them here, only for cooldown (next). A capped
   candidate is non-terminal and returns tomorrow.
4. **Cooldown** (`PolicyConfig.cooldown_minutes`, default 90). Nothing
   fires within that many minutes of the last thing that did —
   *except* a due reminder (`kind="scheduled"`), which is exempt: a
   pill reminder shouldn't wait on a cooldown meant for conversational
   restraint. Non-terminal.
5. **One winner.** Among whatever survives 1–4, the highest-scoring
   candidate fires (`suppressed_by=None`); every other survivor is
   logged `"lost to a higher-scoring candidate this tick"` — non-
   terminal, and explicitly *not* a queue: it competes fresh next tick
   on its own merits, not first-in-line. Scoring (`_score()`): a due
   reminder always outranks a "noticed" insight (medication over
   musing); among "noticed" candidates, the reflected rule's own
   `confidence` (`identity/reflect.py`) breaks ties.

**Every candidate is still logged, allowed or not, with why** (SPEC.md:
"otherwise 'why did it say that' is undebuggable") — now also covering
why something was *held back*: expired, gated, capped, cooled down, or
outscored, each a distinct, inspectable reason. `spoken` is always `0`:
this pass builds the decision machinery and makes it inspectable;
nothing in this codebase calls `say()` from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from saathi.identity.store import IdentityStore
from saathi.initiative.scheduler import EXPIRED_PREFIX, InitiativeCandidate


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


def _score(candidate: InitiativeCandidate) -> float:
    # A due reminder always wins a same-tick competition against a
    # "noticed" musing -- medication over a comment about her mood.
    # Among "noticed" candidates, reflect.py's own confidence breaks
    # ties; it's already a real, derived number (see that module), not
    # invented for this.
    if candidate.kind == "scheduled":
        return float("inf")
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
        days = age.days
        return (
            True,
            f"{EXPIRED_PREFIX} missed its useful window "
            f"({days} days since observed, limit {config.noticed_expiry_days})",
        )
    return False, ""


def _fired_rows(store: IdentityStore) -> list[dict]:
    return [row for row in store.read("initiatives") if row["suppressed_by"] is None]


def _fired_today_count(store: IdentityStore, now: datetime) -> int:
    today = now.date()
    count = 0
    for row in _fired_rows(store):
        ts = datetime.fromisoformat(row["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts.date() == today:
            count += 1
    return count


def _minutes_since_last_fired(store: IdentityStore, now: datetime) -> float | None:
    last_fired_at = None
    for row in _fired_rows(store):
        ts = datetime.fromisoformat(row["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if last_fired_at is None or ts > last_fired_at:
            last_fired_at = ts
    if last_fired_at is None:
        return None
    return (now - last_fired_at).total_seconds() / 60


def evaluate(
    store: IdentityStore,
    candidates: list[InitiativeCandidate],
    context: PolicyContext,
    *,
    now: datetime | None = None,
    config: PolicyConfig | None = None,
) -> list[dict]:
    """Runs the five-step gate above over every candidate and logs each
    one to `initiatives`, allowed, held back, or expired, then returns
    the rows actually written — the thing to read to see what this tick
    decided, without a separate query."""
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

    # 1. Expiry -- terminal, checked before anything else competes.
    survivors = []
    for candidate in candidates:
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

    # 3. Daily cap -- "whatever the score," no exemption for reminders.
    if _fired_today_count(store, now) >= config.daily_cap:
        for candidate in gated:
            _log(candidate, f"daily cap reached ({config.daily_cap}/day)")
        return written

    # 4. Cooldown -- reminders exempt.
    minutes_since_last_fired = _minutes_since_last_fired(store, now)
    eligible = []
    for candidate in gated:
        if (
            candidate.kind != "scheduled"
            and minutes_since_last_fired is not None
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
