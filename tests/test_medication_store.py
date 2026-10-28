"""medication/store.py: the audit log can't be edited, an event leaves
PENDING once, and the device's resident assignment keeps its history."""

import sqlite3
from datetime import datetime

import pytest

from saathi.medication.models import DoseEvent, EventKind, EventSource, EventStatus
from saathi.medication.store import MedicationStore, NotPending

AT = datetime(2026, 10, 8, 14, 5)


@pytest.fixture
def store(tmp_path):
    return MedicationStore(tmp_path / "meds.sqlite3")


def _event(**kw):
    kw.setdefault("dose_id", "r09-2@2026-10-08")
    kw.setdefault("kind", EventKind.GIVEN)
    kw.setdefault("source", EventSource.NURSE_VOICE)
    kw.setdefault("timestamp", AT)
    return DoseEvent(**kw)


def test_the_audit_log_cannot_be_changed_or_deleted(store, tmp_path):
    store.audit("device", "utterance", "r09", text="Bed 12, given")
    raw = sqlite3.connect(tmp_path / "meds.sqlite3")
    with pytest.raises(sqlite3.DatabaseError, match="cannot be changed"):
        raw.execute("UPDATE audit SET actor = 'someone else'")
    with pytest.raises(sqlite3.DatabaseError, match="cannot be changed"):
        raw.execute("DELETE FROM audit")
    assert store.audit_log("r09")[0]["detail"] == {"text": "Bed 12, given"}


def test_an_event_resolves_once_and_is_never_deleted(store, tmp_path):
    saved = store.add_pending(_event(), actor="device")
    store.resolve(saved.id, EventStatus.CONFIRMED, "nurse-ana", AT)
    with pytest.raises(NotPending):
        store.resolve(saved.id, EventStatus.REJECTED, "nurse-ben", AT)
    raw = sqlite3.connect(tmp_path / "meds.sqlite3")
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("UPDATE events SET status = 'pending'")
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("DELETE FROM events")
    event = store.event(saved.id)
    assert event.status is EventStatus.CONFIRMED and event.confirmed_by_nurse == "nurse-ana"


def test_only_pending_events_are_added(store):
    with pytest.raises(ValueError):
        store.add_pending(_event(status="confirmed", confirmed_by_nurse="nurse-ana"), "device")
    with pytest.raises(ValueError):
        store.resolve(1, EventStatus.PENDING, "nurse-ana", AT)


def test_adding_an_event_is_audited_with_its_note(store):
    saved = store.add_pending(_event(kind="side_effect", source="resident_chat",
                                     note="I feel dizzy after lunch"), actor="device")
    [row] = store.audit_log(saved.dose_id)
    assert row["action"] == "event_pending"
    assert row["detail"]["note"] == "I feel dizzy after lunch"
    assert store.events(status=EventStatus.PENDING) == [saved]


def test_the_latest_assignment_wins_and_history_stays(store):
    assert store.assigned_resident() is None
    store.assign("r09", by="nurse-ana")
    store.assign("r10", by="nurse-ben")
    assert store.assigned_resident() == "r10"
    assert [r["subject"] for r in store.audit_log() if r["action"] == "assign_device"] == [
        "r09", "r10"]
