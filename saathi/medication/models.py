"""The medication records: who, what is scheduled, and what happened.

Why plain frozen dataclasses: every adapter (mock, CSV, the vendor's one
later) has to produce exactly these, and the rest of the package -- the
nudges, the refusal line, the dashboard -- only ever sees these. A vendor
SDK's objects never leave its adapter.

Two choices worth keeping:

- `ScheduledDose.dose` ("500 mg") is stored because the eMAR has it and
  the dashboard shows it to nurses. It is never spoken and never sent to
  the model -- the robot never states doses (safety rules, guard.py).
- `purpose` is one plain-language sentence *per language* (a mapping),
  not one string. The resident hears it in her preferred language, else
  her fallback; the model never translates medical text, because a
  translation that drifts is an invented medical claim (SPEC.md,
  "Phrasing may not invent"). Lost: a single `purpose_plain_text` that
  the model translates on the fly.

`DoseEvent.confirmed_by_nurse` holds *which* nurse confirmed, not a
bool: the device can't tell a nurse's voice from anyone else's, so the
trust is the nurse who signed in and pressed confirm, and the audit has
to name them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from typing import Mapping

# The languages a resident can be assigned (owner, 2026-10-08). Singlish
# is every resident's fallback unless set otherwise. These are what the
# medication records hold; whether Saathi can *speak* each one is
# voice/language.py's business and Phase 3's (Hokkien has no cloud voice
# yet -- DECISIONS 2026-10-08).
MEDICATION_LANGUAGES = ("singlish", "cantonese", "hokkien", "tamil")
DEFAULT_FALLBACK_LANGUAGE = "singlish"


class UnknownLanguage(ValueError):
    pass


def check_language(language: str) -> str:
    value = (language or "").strip().lower()
    if value not in MEDICATION_LANGUAGES:
        raise UnknownLanguage(
            f"{language!r} isn't one of {', '.join(MEDICATION_LANGUAGES)}"
        )
    return value


@dataclass(frozen=True)
class Resident:
    id: str
    bed: str
    name: str
    preferred_language: str
    fallback_language: str = DEFAULT_FALLBACK_LANGUAGE

    def __post_init__(self) -> None:
        object.__setattr__(self, "preferred_language", check_language(self.preferred_language))
        object.__setattr__(self, "fallback_language", check_language(self.fallback_language))


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"window ends ({self.end}) before it starts ({self.start})")


@dataclass(frozen=True)
class ScheduledDose:
    id: str
    resident_id: str
    medication: str
    dose: str  # for nurses' eyes only: never spoken, never sent to a model
    time_window: TimeWindow
    purpose: Mapping[str, str] = field(default_factory=dict)  # language -> sentence

    @property
    def day(self) -> date:
        return self.time_window.start.date()

    def purpose_for(self, resident: Resident) -> tuple[str, str] | None:
        """(language, sentence) in her preferred language, else her
        fallback; None when neither exists -- then the nurse explains."""
        for language in (resident.preferred_language, resident.fallback_language):
            text = (self.purpose.get(language) or "").strip()
            if text:
                return language, text
        return None


class EventKind(str, Enum):
    GIVEN = "given"
    REFUSED = "refused"
    MISSED = "missed"
    SIDE_EFFECT = "side_effect"


# What counts as the dose having been dealt with (a side effect doesn't).
ADMINISTRATION_KINDS = frozenset({EventKind.GIVEN, EventKind.REFUSED, EventKind.MISSED})


class EventSource(str, Enum):
    NURSE_DASHBOARD = "nurse_dashboard"
    NURSE_VOICE = "nurse_voice"
    RESIDENT_CHAT = "resident_chat"
    REFUSAL_SUPPORT = "refusal_support"
    EMAR = "emar"  # already in the eMAR when we read it


class EventStatus(str, Enum):
    PENDING = "pending"  # heard or flagged; not in the eMAR
    CONFIRMED = "confirmed"  # a nurse confirmed it on the dashboard
    REJECTED = "rejected"  # a nurse said it was wrong


@dataclass(frozen=True)
class DoseEvent:
    dose_id: str
    kind: EventKind
    source: EventSource
    timestamp: datetime
    status: EventStatus = EventStatus.PENDING
    confirmed_by_nurse: str | None = None  # the nurse's id, set only by writer.py
    note: str | None = None  # e.g. the exact transcript behind a side-effect flag
    id: int | None = None  # the local store's row id once saved

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", EventKind(self.kind))
        object.__setattr__(self, "source", EventSource(self.source))
        object.__setattr__(self, "status", EventStatus(self.status))
        if self.status is EventStatus.CONFIRMED and not self.confirmed_by_nurse:
            raise ValueError("a confirmed event names the nurse who confirmed it")

    @property
    def confirmed(self) -> bool:
        return self.status is EventStatus.CONFIRMED

    def with_id(self, row_id: int) -> "DoseEvent":
        return replace(self, id=row_id)


@dataclass(frozen=True)
class AdministrationStatus:
    """What the eMAR says happened to one dose: the latest confirmed
    given/refused/missed, or nothing yet (`event` None)."""

    dose_id: str
    event: DoseEvent | None

    @property
    def logged(self) -> bool:
        return self.event is not None

    @property
    def state(self) -> str:
        return self.event.kind.value if self.event else "not_logged"
