"""The only path from "heard" to "in the eMAR": a nurse confirms.

Why one module owns it: "nothing is written to the eMAR without nurse
confirmation" is only true if there is exactly one place that writes,
and that place demands the confirmation. `confirm()` takes a
`NurseConfirmation` -- built by the staff dashboard's signed-in confirm
handler (Phase 2), naming the nurse -- and nothing else in the package
calls `EMARAdapter.record_event` (tests/test_medication_emar.py greps
for it). The adapters refuse unconfirmed events as well (emar.py), so a
bypass would have to defeat both.

Order: the eMAR write first, then the local event is marked confirmed.
If the eMAR write fails the event stays PENDING and the nurse sees it
still waiting -- the safe side. The reverse order could show "confirmed"
on the dashboard for a dose the eMAR never got.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from saathi.medication.emar import EMARAdapter
from saathi.medication.models import DoseEvent, EventStatus
from saathi.medication.store import MedicationStore, NotPending


@dataclass(frozen=True)
class NurseConfirmation:
    nurse_id: str
    at: datetime

    def __post_init__(self) -> None:
        if not (self.nurse_id or "").strip():
            raise ValueError("a confirmation names the nurse")


def confirm(store: MedicationStore, emar: EMARAdapter, event_id: int,
            confirmation: NurseConfirmation) -> DoseEvent:
    pending = store.event(event_id)
    if pending.status is not EventStatus.PENDING:
        raise NotPending(f"event {event_id} isn't waiting for a nurse")
    confirmed = replace(pending, status=EventStatus.CONFIRMED,
                        confirmed_by_nurse=confirmation.nurse_id)
    try:
        emar.record_event(pending.dose_id, confirmed)
    except Exception as exc:
        store.audit(confirmation.nurse_id, "emar_write_failed", pending.dose_id,
                    at=confirmation.at, event_id=event_id, error=str(exc))
        raise
    store.resolve(event_id, EventStatus.CONFIRMED, confirmation.nurse_id, confirmation.at)
    store.audit(confirmation.nurse_id, "event_confirmed", pending.dose_id, at=confirmation.at,
                event_id=event_id, kind=pending.kind.value)
    return confirmed


def reject(store: MedicationStore, event_id: int, confirmation: NurseConfirmation,
           reason: str = "") -> None:
    """The nurse says it didn't happen (misheard, wrong bed): nothing
    goes to the eMAR, and the event can't be confirmed later."""
    pending = store.event(event_id)
    store.resolve(event_id, EventStatus.REJECTED, confirmation.nurse_id, confirmation.at)
    store.audit(confirmation.nurse_id, "event_rejected", pending.dose_id, at=confirmation.at,
                event_id=event_id, reason=reason)
