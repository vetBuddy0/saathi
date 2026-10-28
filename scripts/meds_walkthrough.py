"""Phase 1 walk-through: a heard "Bed 6, given" waits as PENDING, the
eMAR refuses it until a nurse confirms, then it's in the eMAR and the
audit log shows every step."""
from datetime import datetime

from saathi.config import Config
from saathi.medication.cli import open_emar, open_store
from saathi.medication.emar import UnconfirmedWrite
from saathi.medication.models import DoseEvent, EventKind, EventSource
from saathi.medication.writer import NurseConfirmation, confirm

data = Config.load().data_dir
emar, store = open_emar(data), open_store(data)
lee = next(r for r in emar.list_residents() if r.bed == "6")
dose = emar.get_schedule(lee.id, datetime.now().date())[6]  # the 2pm Paracetamol
print("dose:", dose.id, dose.medication, "->", emar.get_administration_status(dose.id).state)

pending = store.add_pending(DoseEvent(dose_id=dose.id, kind=EventKind.GIVEN,
                                      source=EventSource.NURSE_VOICE, timestamp=datetime.now(),
                                      note="Bed 6, given"), actor="device")
print("heard, pending:", pending.status.value)
try:
    emar.record_event(dose.id, pending)
except UnconfirmedWrite as exc:
    print("eMAR refused the unconfirmed write:", exc)
print("eMAR still says:", emar.get_administration_status(dose.id).state)

confirm(store, emar, pending.id, NurseConfirmation("nurse-ana", datetime.now()))
status = emar.get_administration_status(dose.id)
print("after the nurse confirms:", status.state, "by", status.event.confirmed_by_nurse)
for row in store.audit_log(dose.id):
    print("audit:", row["ts"][:19], row["actor"], row["action"])
