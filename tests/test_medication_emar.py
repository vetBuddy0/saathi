"""medication/emar.py and writer.py: both adapters answer the same
contract, and nothing reaches the eMAR without a nurse.

The contract tests run once per adapter (mock, CSV) over the same small
ward, so a future vendor adapter is one more fixture param."""

import csv
import re
from datetime import date, datetime, time
from pathlib import Path

import pytest

from saathi.medication.emar import (
    CSV_COLUMNS,
    CSVImportAdapter,
    MockEMARAdapter,
    UnconfirmedWrite,
    UnknownDose,
    parse_export,
)
from saathi.medication.models import (
    DoseEvent,
    EventKind,
    EventSource,
    EventStatus,
    Resident,
    UnknownLanguage,
)
from saathi.medication.store import MedicationStore, NotPending
from saathi.medication.writer import NurseConfirmation, confirm, reject

DAY = date(2026, 10, 8)
AT = datetime(2026, 10, 8, 14, 5)
PURPOSE = {"singlish": "This one helps keep your blood sugar steady.",
           "cantonese": "呢隻藥係幫你穩定血糖嘅。"}


def _row(**overrides):
    row = {c: "" for c in CSV_COLUMNS}
    row.update(resident_id="r09", bed="12", name="Leung Kam Wah",
               preferred_language="cantonese", fallback_language="singlish",
               dose_id="r09-2", medication="Metformin", dose="500 mg",
               window_start="13:30", window_end="14:30",
               purpose_singlish=PURPOSE["singlish"], purpose_cantonese=PURPOSE["cantonese"])
    row.update(overrides)
    return row


def _write_csv(path: Path, rows) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture(params=["mock", "csv"])
def emar(request, tmp_path):
    if request.param == "mock":
        mock = MockEMARAdapter(tmp_path / "mock.sqlite3")
        mock.add_resident(Resident("r09", "12", "Leung Kam Wah", "cantonese"))
        mock.add_plan("r09-1", "r09", "Omeprazole", "20 mg", time(8), time(9),
                      {"singlish": "This one protects your stomach from too much acid."})
        mock.add_plan("r09-2", "r09", "Metformin", "500 mg", time(13, 30), time(14, 30), PURPOSE)
        return mock
    path = _write_csv(tmp_path / "export.csv", [
        _row(dose_id="r09-1", medication="Omeprazole", dose="20 mg",
             window_start="08:00", window_end="09:00",
             purpose_singlish="This one protects your stomach from too much acid.",
             purpose_cantonese=""),
        _row(),
    ])
    adapter = CSVImportAdapter(path, tmp_path / "outbox.csv")
    assert adapter.report.ok, adapter.report.errors
    return adapter


def _confirmed(dose_id="r09-2@2026-10-08", kind=EventKind.GIVEN, nurse="nurse-ana"):
    return DoseEvent(dose_id=dose_id, kind=kind, source=EventSource.NURSE_DASHBOARD,
                     timestamp=AT, status=EventStatus.CONFIRMED, confirmed_by_nurse=nurse)


# -- the contract, per adapter ------------------------------------------------

def test_lists_residents_with_their_languages(emar):
    [resident] = emar.list_residents()
    assert (resident.bed, resident.preferred_language, resident.fallback_language) == (
        "12", "cantonese", "singlish")


def test_schedule_is_the_day_asked_for_in_window_order(emar):
    doses = emar.get_schedule("r09", DAY)
    assert [d.id for d in doses] == ["r09-1@2026-10-08", "r09-2@2026-10-08"]
    two_pm = doses[1]
    assert two_pm.time_window.start == datetime(2026, 10, 8, 13, 30)
    assert two_pm.time_window.end == datetime(2026, 10, 8, 14, 30)
    assert two_pm.dose == "500 mg" and two_pm.purpose["cantonese"] == PURPOSE["cantonese"]
    assert emar.get_schedule("nobody", DAY) == []


def test_purpose_falls_back_to_her_fallback_language(emar):
    resident = emar.list_residents()[0]
    morning, two_pm = emar.get_schedule("r09", DAY)
    assert two_pm.purpose_for(resident) == ("cantonese", PURPOSE["cantonese"])
    assert morning.purpose_for(resident)[0] == "singlish"


def test_a_new_dose_is_not_logged(emar):
    status = emar.get_administration_status("r09-2@2026-10-08")
    assert not status.logged and status.state == "not_logged"


def test_a_confirmed_event_is_recorded_and_read_back(emar):
    emar.record_event("r09-2@2026-10-08", _confirmed())
    status = emar.get_administration_status("r09-2@2026-10-08")
    assert status.state == "given" and status.event.confirmed_by_nurse == "nurse-ana"
    # Another day's dose is untouched.
    assert not emar.get_administration_status("r09-2@2026-10-09").logged


def test_a_side_effect_is_not_an_administration(emar):
    event = _confirmed(kind=EventKind.SIDE_EFFECT)
    emar.record_event(event.dose_id, event)
    assert not emar.get_administration_status(event.dose_id).logged


@pytest.mark.parametrize("status", [EventStatus.PENDING, EventStatus.REJECTED])
def test_the_emar_refuses_anything_a_nurse_did_not_confirm(emar, status):
    event = DoseEvent(dose_id="r09-2@2026-10-08", kind=EventKind.GIVEN,
                      source=EventSource.NURSE_VOICE, timestamp=AT, status=status,
                      confirmed_by_nurse="nurse-ana" if status is EventStatus.REJECTED else None)
    with pytest.raises(UnconfirmedWrite):
        emar.record_event(event.dose_id, event)
    assert not emar.get_administration_status(event.dose_id).logged


def test_unknown_doses_are_refused(emar):
    with pytest.raises(UnknownDose):
        emar.get_administration_status("r99-1@2026-10-08")
    with pytest.raises(UnknownDose):
        emar.record_event("no-day", _confirmed(dose_id="no-day"))


def test_a_confirmed_event_must_name_its_nurse():
    with pytest.raises(ValueError):
        DoseEvent(dose_id="x@2026-10-08", kind="given", source="nurse_voice",
                  timestamp=AT, status="confirmed")


# -- the writer: heard -> pending -> nurse confirms -> eMAR ---------------------

def _pending(store, kind=EventKind.GIVEN):
    return store.add_pending(
        DoseEvent(dose_id="r09-2@2026-10-08", kind=kind, source=EventSource.NURSE_VOICE,
                  timestamp=AT, note="Bed 12, given"),
        actor="device",
    )


def test_pending_reaches_the_emar_only_when_a_nurse_confirms(emar, tmp_path):
    store = MedicationStore(tmp_path / "meds.sqlite3")
    pending = _pending(store)
    assert not emar.get_administration_status(pending.dose_id).logged
    confirm(store, emar, pending.id, NurseConfirmation("nurse-ana", AT))
    assert emar.get_administration_status(pending.dose_id).event.confirmed_by_nurse == "nurse-ana"
    assert store.event(pending.id).status is EventStatus.CONFIRMED
    actions = [row["action"] for row in store.audit_log(pending.dose_id)]
    assert actions == ["event_pending", "event_confirmed"]
    with pytest.raises(NotPending):  # once only
        confirm(store, emar, pending.id, NurseConfirmation("nurse-ben", AT))


def test_a_rejected_event_never_reaches_the_emar(emar, tmp_path):
    store = MedicationStore(tmp_path / "meds.sqlite3")
    pending = _pending(store)
    reject(store, pending.id, NurseConfirmation("nurse-ana", AT), reason="wrong bed")
    with pytest.raises(NotPending):
        confirm(store, emar, pending.id, NurseConfirmation("nurse-ana", AT))
    assert not emar.get_administration_status(pending.dose_id).logged


def test_a_failed_emar_write_leaves_the_event_waiting(tmp_path):
    class Down:
        def record_event(self, dose_id, event):
            raise ConnectionError("eMAR unreachable")

    store = MedicationStore(tmp_path / "meds.sqlite3")
    pending = _pending(store)
    with pytest.raises(ConnectionError):
        confirm(store, Down(), pending.id, NurseConfirmation("nurse-ana", AT))
    assert store.event(pending.id).status is EventStatus.PENDING
    assert store.audit_log(pending.dose_id)[-1]["action"] == "emar_write_failed"


def test_a_confirmation_names_a_nurse():
    with pytest.raises(ValueError):
        NurseConfirmation("  ", AT)


def test_only_the_writer_calls_record_event():
    package = Path(__file__).parent.parent / "saathi"
    callers = sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if re.search(r"\.record_event\(", path.read_text(encoding="utf-8"))
    )
    assert callers == ["medication/writer.py"]


# -- the CSV import's door -----------------------------------------------------

def test_bad_rows_are_reported_with_their_line(tmp_path):
    path = _write_csv(tmp_path / "bad.csv", [
        _row(),                                                    # line 2: fine
        _row(dose_id="r09-3", preferred_language="klingon"),       # 3
        _row(dose_id="r09-4", window_start="15:00", window_end="14:00"),  # 4
        _row(dose_id="r09-5", purpose_singlish="Take 2 tablets for the pain."),  # 5
        _row(dose_id="r09-6", purpose_cantonese="唔舒服就唔使食。"),  # 6
        _row(dose_id="r09-2"),                                     # 7: repeated
        _row(dose_id="r10-1", resident_id="r10", name="Ahmad Rahim"),  # 8: bed 12 taken
        _row(dose_id="r09-7", medication=""),                      # 9
    ])
    report, _residents, plans = parse_export(path)
    lines = dict(report.errors)
    assert sorted(lines) == [3, 4, 5, 6, 7, 8, 9]
    assert "klingon" in lines[3]
    assert "dose" in lines[5] and "instruction" in lines[6]
    assert "bed 12" in lines[8]
    assert list(plans) == ["r09-2"]


def test_missing_columns_reject_the_whole_file(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("name,bed\nTan,1\n", encoding="utf-8")
    report, _, _ = parse_export(path)
    assert not report.ok and report.errors[0][0] == 1


def test_a_dated_row_is_only_that_day(tmp_path):
    path = _write_csv(tmp_path / "dated.csv", [_row(date="2026-10-08")])
    adapter = CSVImportAdapter(path, tmp_path / "outbox.csv")
    assert len(adapter.get_schedule("r09", DAY)) == 1
    assert adapter.get_schedule("r09", date(2026, 10, 9)) == []


def test_the_mock_refuses_a_purpose_that_states_a_dose(tmp_path):
    mock = MockEMARAdapter(tmp_path / "mock.sqlite3")
    with pytest.raises(ValueError):
        mock.add_plan("p", "r1", "Paracetamol", "1 g", time(8), time(9),
                      {"singlish": "Two tablets when you have pain."})
    with pytest.raises(UnknownLanguage):
        mock.add_plan("p", "r1", "Paracetamol", "1 g", time(8), time(9), {"french": "Pour"})


def test_residents_are_listed_by_bed_number(tmp_path):
    mock = MockEMARAdapter(tmp_path / "mock.sqlite3")
    for rid, bed in [("a", "10"), ("b", "2"), ("c", "Isolation"), ("d", "1")]:
        mock.add_resident(Resident(rid, bed, rid, "singlish"))
    assert [r.bed for r in mock.list_residents()] == ["1", "2", "10", "Isolation"]
