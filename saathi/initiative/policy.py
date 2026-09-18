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

**Every proposed initiative is logged, allowed or not** (SPEC.md:
"Otherwise 'why did it say that' is undebuggable"). `evaluate()` writes
one `initiatives` row per candidate either way: `suppressed_by=None` for
an allowed one, or the specific reason it was held back. `spoken` is
always `0` here, deliberately, for both — this pass builds the decision
machinery and makes it inspectable; nothing in this codebase calls
`say()` from here. Wiring an allowed initiative to actual speech is a
separate, later step, not decided by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from saathi.identity.store import IdentityStore
from saathi.initiative.scheduler import InitiativeCandidate


@dataclass(frozen=True)
class PolicyContext:
    """Every field defaults to "no confirmed signal", which
    `should_speak()` treats as a reason to suppress, not a reason to
    proceed — see this module's docstring."""

    presence: bool | None = None  # True only if something has confirmed she's there
    quiet_hours: bool = False
    busy: bool = False  # television, a call, anything already happening


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


def evaluate(
    store: IdentityStore,
    candidates: list[InitiativeCandidate],
    context: PolicyContext,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Runs `should_speak()` over every candidate and logs each one to
    `initiatives`, allowed or not, then returns the rows actually
    written — the thing to read to see what this pass decided, without
    a separate query. `spoken` is always `0`: see this module's
    docstring for why that's deliberate, not an oversight."""
    now = now or datetime.now(timezone.utc)
    written = []
    for candidate in candidates:
        decision = should_speak(candidate, context)
        row_id = store.append(
            "initiatives",
            ts=now.isoformat(),
            kind=candidate.kind,
            reason=candidate.reason,
            source_episode=candidate.source_episode,
            spoken=0,
            suppressed_by=None if decision.speak else decision.reason,
        )
        written.extend(store.read("initiatives", id=row_id))
    return written
