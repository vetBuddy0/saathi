"""Saathi's own medication record: which resident this device belongs
to, what was heard or flagged and is waiting for a nurse, and the audit
log of every medication-related utterance and action.

Why it exists apart from the eMAR: the eMAR only ever holds what a nurse
confirmed (emar.py). Everything before that -- "Bed 12, given" heard by
voice, a symptom she mentioned -- has to live somewhere a nurse can see
it and decide, and that is here, as a PENDING event.

The audit log cannot be edited. SQLite triggers reject every UPDATE and
DELETE on `audit`, and this class has no method that tries; an event
can leave PENDING exactly once (to CONFIRMED or REJECTED) and a trigger
rejects any change after that. Lost: a plain table with "please don't"
in a docstring -- an audit log that a bug can rewrite isn't one.

One device, one resident (owner, 2026-10-08): `assignment` is an
append-only log, latest row wins, like `preferences` in the identity
store, so reassigning a device leaves a record of who it was before.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from saathi.medication.models import DoseEvent, EventStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assignment (
    id INTEGER PRIMARY KEY, resident_id TEXT NOT NULL,
    assigned_at TEXT NOT NULL, assigned_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, dose_id TEXT NOT NULL, kind TEXT NOT NULL,
    source TEXT NOT NULL, timestamp TEXT NOT NULL, status TEXT NOT NULL,
    confirmed_by_nurse TEXT, resolved_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, subject TEXT, detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS events_resolve_once BEFORE UPDATE ON events
WHEN OLD.status != 'pending'
BEGIN SELECT RAISE(ABORT, 'a resolved event cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are never deleted'); END;
"""


class NotPending(ValueError):
    pass


class MedicationStore:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # -- audit ------------------------------------------------------------

    def audit(self, actor: str, action: str, subject: str | None = None,
              at: datetime | None = None, **detail: Any) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO audit (ts, actor, action, subject, detail) VALUES (?, ?, ?, ?, ?)",
                ((at or datetime.now()).isoformat(), actor, action, subject,
                 json.dumps(detail, ensure_ascii=False, default=str)),
            )
        return cur.lastrowid

    def audit_log(self, subject: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM audit", ()
        if subject is not None:
            sql, args = sql + " WHERE subject = ?", (subject,)
        rows = self._conn.execute(sql + " ORDER BY id", args).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    # -- which resident this device is for ----------------------------------

    def assign(self, resident_id: str, by: str, at: datetime | None = None) -> None:
        at = at or datetime.now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO assignment (resident_id, assigned_at, assigned_by) VALUES (?, ?, ?)",
                (resident_id, at.isoformat(), by),
            )
        self.audit(by, "assign_device", resident_id, at=at)

    def assigned_resident(self) -> str | None:
        row = self._conn.execute(
            "SELECT resident_id FROM assignment ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["resident_id"] if row else None

    # -- events waiting for a nurse ---------------------------------------

    def add_pending(self, event: DoseEvent, actor: str) -> DoseEvent:
        if event.status is not EventStatus.PENDING:
            raise ValueError("only a pending event is added; confirming is writer.py's")
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO events (dose_id, kind, source, timestamp, status, note)"
                " VALUES (?, ?, ?, ?, 'pending', ?)",
                (event.dose_id, event.kind.value, event.source.value,
                 event.timestamp.isoformat(), event.note),
            )
        saved = event.with_id(cur.lastrowid)
        self.audit(actor, "event_pending", event.dose_id, at=event.timestamp,
                   event_id=saved.id, kind=event.kind.value, source=event.source.value,
                   note=event.note)
        return saved

    def event(self, event_id: int) -> DoseEvent:
        row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise LookupError(f"no event {event_id}")
        return _event(row)

    def events(self, dose_id: str | None = None,
               status: EventStatus | None = None) -> list[DoseEvent]:
        clauses, args = [], []
        if dose_id is not None:
            clauses.append("dose_id = ?")
            args.append(dose_id)
        if status is not None:
            clauses.append("status = ?")
            args.append(EventStatus(status).value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM events{where} ORDER BY id", args).fetchall()
        return [_event(r) for r in rows]

    def resolve(self, event_id: int, status: EventStatus, nurse_id: str, at: datetime) -> None:
        """PENDING -> CONFIRMED or REJECTED, once. writer.py calls this;
        nothing else should."""
        status = EventStatus(status)
        if status is EventStatus.PENDING:
            raise ValueError("resolving means confirming or rejecting")
        with self._conn:
            cur = self._conn.execute(
                "UPDATE events SET status = ?, confirmed_by_nurse = ?, resolved_at = ?"
                " WHERE id = ? AND status = 'pending'",
                (status.value, nurse_id, at.isoformat(), event_id),
            )
        if cur.rowcount != 1:
            raise NotPending(f"event {event_id} isn't waiting for a nurse")

    def close(self) -> None:
        self._conn.close()


def _event(row: sqlite3.Row) -> DoseEvent:
    return DoseEvent(
        id=row["id"], dose_id=row["dose_id"], kind=row["kind"], source=row["source"],
        timestamp=datetime.fromisoformat(row["timestamp"]), status=row["status"],
        confirmed_by_nurse=row["confirmed_by_nurse"], note=row["note"],
    )
