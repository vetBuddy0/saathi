"""Decides *when* to speak first — scheduled, event, and noticed
triggers (SPEC.md, "Initiative"): three kinds, and the third is the
product.

This module only *proposes* candidates — a kind, a plain-language
reason, and (for "noticed") which episode it traces back to. It never
decides whether to actually say one out loud; that is entirely
`policy.py`'s job ("a reason to speak, not the absence of a reason to
stay quiet"), run separately so scheduling and restraint stay two
things a reader can reason about independently.

**Scheduled** — reminders (`reminders` table) whose `due_at` has passed.
Real and working: reads what checkpoint 1/2 already built.

**Event** — sensor-triggered (she arrived, a call came). Always returns
`[]` today, honestly: nothing in this codebase does presence or call
detection yet. Kept as its own function, not skipped, so the three-kind
structure stays explicit and this is a one-function change once a real
sensor exists, not a redesign.

**Noticed** — memory. Queries `identity/reflect.py`'s output (`rules`)
for active, reasonably confident insights that haven't already produced
a candidate — this is the mechanism SPEC.md calls "the product": "you
said the scan was today."

Deduplication for both Scheduled and Noticed reads `initiatives`
directly rather than mutating `reminders`/`rules` (`IdentityStore` has
no update — see `identity/profile.py`'s docstring for the same
constraint hitting rule retraction). A reminder or rule that's already
produced an `initiatives` row is skipped on later runs; this needs each
proposal's origin to be findable in `initiatives`, which is exactly what
`_REMINDER_TAG`/`source_episode` are for below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from saathi.identity.store import IdentityStore

# Confidence floor for a "noticed" candidate -- see reflect.py's
# docstring for how confidence is derived (more corroborating episodes,
# higher confidence). A device that raises every half-grounded insight
# unprompted is exactly the "comments on everything" failure SPEC.md
# warns policy.py exists to prevent; scheduler.py holds its own floor
# too, since a barely-grounded insight isn't "noticed" so much as
# guessed.
NOTICED_CONFIDENCE_FLOOR = 0.5

# Embedded in a scheduled candidate's `reason` so a later run can tell
# "this reminder already produced a candidate" without IdentityStore
# needing a reminder_id column on `initiatives` (which it doesn't have,
# and adding one is a schema question, not this module's to decide).
_REMINDER_TAG_RE = re.compile(r"\[reminder_id=(\d+)\]")


@dataclass(frozen=True)
class InitiativeCandidate:
    kind: str  # "scheduled" | "event" | "noticed"
    reason: str
    source_episode: int | None = None


def _already_proposed_reminder_ids(store: IdentityStore) -> set[int]:
    ids = set()
    for row in store.read("initiatives", kind="scheduled"):
        match = _REMINDER_TAG_RE.search(row["reason"] or "")
        if match:
            ids.add(int(match.group(1)))
    return ids


def scheduled_candidates(
    store: IdentityStore, *, now: datetime | None = None
) -> list[InitiativeCandidate]:
    now = now or datetime.now(timezone.utc)
    already_proposed = _already_proposed_reminder_ids(store)
    candidates = []
    for reminder in store.read("reminders", active=1):
        if reminder["id"] in already_proposed:
            continue
        due_at = datetime.fromisoformat(reminder["due_at"])
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        if due_at > now:
            continue
        candidates.append(
            InitiativeCandidate(
                kind="scheduled",
                reason=f"Reminder due: {reminder['text']} [reminder_id={reminder['id']}]",
            )
        )
    return candidates


def event_candidates(store: IdentityStore) -> list[InitiativeCandidate]:
    """Always `[]` today — see this module's docstring. `store` is
    accepted (unused) so this has the same call shape as the other two
    sources and a real implementation later doesn't change the call
    site."""
    return []


def _already_proposed_source_episodes(store: IdentityStore) -> set[int]:
    return {
        row["source_episode"]
        for row in store.read("initiatives", kind="noticed")
        if row["source_episode"] is not None
    }


def noticed_candidates(
    store: IdentityStore, *, confidence_floor: float = NOTICED_CONFIDENCE_FLOOR
) -> list[InitiativeCandidate]:
    already_proposed = _already_proposed_source_episodes(store)
    candidates = []
    for rule in store.read("rules", active=1):
        if rule["source_episode"] is None or rule["source_episode"] in already_proposed:
            continue
        if (rule["confidence"] or 0.0) < confidence_floor:
            continue
        candidates.append(
            InitiativeCandidate(
                kind="noticed",
                reason=rule["text"],
                source_episode=rule["source_episode"],
            )
        )
    return candidates


def propose_candidates(
    store: IdentityStore, *, now: datetime | None = None
) -> list[InitiativeCandidate]:
    """All three kinds, in SPEC.md's own order. A candidate here is not
    a decision to speak — `policy.py` (never this module) decides that."""
    return [
        *scheduled_candidates(store, now=now),
        *event_candidates(store),
        *noticed_candidates(store),
    ]
