"""`EMARAdapter`: the one seam between Saathi and the home's eMAR, whose
vendor we don't know yet.

Why an adapter and not a vendor client: the vendor is unknown, and
picking one is a decision for the owner, not for code. Everything else
in the package talks to this protocol; the vendor adapter, when there
is one, is one more class here.

The interface is the owner's three calls plus `list_residents()` -- the
overdue check (Phase 2) has to walk every resident's schedule, and the
three calls can't say who the residents are (DECISIONS 2026-10-08).

**Nothing is written without a nurse.** Every adapter's `record_event`
refuses an event that isn't `CONFIRMED` with a nurse named
(`require_confirmed`), and `medication/writer.py` is the only caller in
the package (tests/test_medication_emar.py checks both). That is two
locks on purpose: one at the door of the eMAR, one on who may knock.

Two implementations:

- `MockEMARAdapter`: a local SQLite standing in for the vendor, seeded
  by `seed.py` with eleven fake residents on daily rounds. Doses are daily
  plans; a dose's id is `"<plan>@<YYYY-MM-DD>"`, the plan repeated on
  the day asked for.
- `CSVImportAdapter`: staff upload an eMAR export. Read-only by nature
  -- a CSV can't write back into a vendor's system -- so confirmed
  events go to an *outbox* CSV for staff to enter into the eMAR, and
  `get_administration_status` reads that outbox back. Rows that don't
  parse are reported with their line number, never skipped silently,
  and a purpose sentence that states a dose or an instruction is a bad
  row (guard.py). Lost: accepting the good rows of a bad file without
  saying which were dropped -- a dose missing from the schedule is a
  dose nobody is nudged about.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, Protocol

from saathi.medication.guard import dose_or_instruction
from saathi.medication.models import (
    ADMINISTRATION_KINDS,
    DEFAULT_FALLBACK_LANGUAGE,
    MEDICATION_LANGUAGES,
    AdministrationStatus,
    DoseEvent,
    EventStatus,
    Resident,
    ScheduledDose,
    TimeWindow,
    UnknownLanguage,
)


class UnconfirmedWrite(PermissionError):
    """An attempt to write to the eMAR without a nurse's confirmation."""


class UnknownDose(LookupError):
    pass


def require_confirmed(event: DoseEvent) -> None:
    if event.status is not EventStatus.CONFIRMED or not event.confirmed_by_nurse:
        raise UnconfirmedWrite(
            f"dose {event.dose_id}: only a nurse-confirmed event is written to the eMAR"
        )


class EMARAdapter(Protocol):
    def list_residents(self) -> list[Resident]: ...

    def get_schedule(self, resident_id: str, day: date) -> list[ScheduledDose]: ...

    def get_administration_status(self, dose_id: str) -> AdministrationStatus: ...

    def record_event(self, dose_id: str, event: DoseEvent) -> None: ...


def split_dose_id(dose_id: str) -> tuple[str, date]:
    plan, sep, day = dose_id.rpartition("@")
    if not sep or not plan:
        raise UnknownDose(dose_id)
    try:
        return plan, date.fromisoformat(day)
    except ValueError:
        raise UnknownDose(dose_id) from None


def _latest_administration(events: Iterable[DoseEvent], dose_id: str) -> AdministrationStatus:
    latest = None
    for event in events:
        if event.kind in ADMINISTRATION_KINDS and event.confirmed:
            if latest is None or event.timestamp >= latest.timestamp:
                latest = event
    return AdministrationStatus(dose_id=dose_id, event=latest)


def bed_order(resident: Resident) -> tuple:
    """Bed 2 before bed 10: numeric beds by number, then the rest."""
    bed = resident.bed
    return (0, int(bed), "") if bed.isdigit() else (1, 0, bed)


def _window(day: date, start: time, end: time) -> TimeWindow:
    return TimeWindow(datetime.combine(day, start), datetime.combine(day, end))


# -- the mock ---------------------------------------------------------------

_MOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS residents (
    id TEXT PRIMARY KEY, bed TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    preferred_language TEXT NOT NULL, fallback_language TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dose_plans (
    id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
    medication TEXT NOT NULL, dose TEXT NOT NULL,
    window_start TEXT NOT NULL, window_end TEXT NOT NULL,
    purpose TEXT NOT NULL  -- JSON {language: sentence}
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY, dose_id TEXT NOT NULL, kind TEXT NOT NULL,
    source TEXT NOT NULL, timestamp TEXT NOT NULL, nurse TEXT NOT NULL, note TEXT
);
"""


class MockEMARAdapter:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_MOCK_SCHEMA)

    # Seeding (seed.py) and tests only; a real eMAR is filled by the home.
    def add_resident(self, resident: Resident) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO residents VALUES (?, ?, ?, ?, ?)",
                (resident.id, resident.bed, resident.name,
                 resident.preferred_language, resident.fallback_language),
            )

    def add_plan(self, plan_id: str, resident_id: str, medication: str, dose: str,
                 start: time, end: time, purpose: dict[str, str]) -> None:
        for language, text in purpose.items():
            if language not in MEDICATION_LANGUAGES:
                raise UnknownLanguage(language)
            reason = dose_or_instruction(text)
            if reason:
                raise ValueError(f"plan {plan_id}: purpose ({language}) states a {reason}")
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO dose_plans VALUES (?, ?, ?, ?, ?, ?, ?)",
                (plan_id, resident_id, medication, dose, start.isoformat("minutes"),
                 end.isoformat("minutes"), json.dumps(purpose, ensure_ascii=False)),
            )

    def is_empty(self) -> bool:
        return self._conn.execute("SELECT COUNT(*) FROM residents").fetchone()[0] == 0

    def list_residents(self) -> list[Resident]:
        rows = self._conn.execute("SELECT * FROM residents").fetchall()
        return sorted((Resident(**dict(row)) for row in rows), key=bed_order)

    def get_schedule(self, resident_id: str, day: date) -> list[ScheduledDose]:
        rows = self._conn.execute(
            "SELECT * FROM dose_plans WHERE resident_id = ? ORDER BY window_start, id",
            (resident_id,),
        ).fetchall()
        return [self._dose(row, day) for row in rows]

    def _dose(self, row: sqlite3.Row, day: date) -> ScheduledDose:
        return ScheduledDose(
            id=f"{row['id']}@{day.isoformat()}",
            resident_id=row["resident_id"],
            medication=row["medication"],
            dose=row["dose"],
            time_window=_window(day, time.fromisoformat(row["window_start"]),
                                time.fromisoformat(row["window_end"])),
            purpose=json.loads(row["purpose"]),
        )

    def _require_dose(self, dose_id: str) -> None:
        plan, _day = split_dose_id(dose_id)
        if not self._conn.execute("SELECT 1 FROM dose_plans WHERE id = ?", (plan,)).fetchone():
            raise UnknownDose(dose_id)

    def get_administration_status(self, dose_id: str) -> AdministrationStatus:
        self._require_dose(dose_id)
        rows = self._conn.execute("SELECT * FROM records WHERE dose_id = ?", (dose_id,)).fetchall()
        events = [
            DoseEvent(dose_id=r["dose_id"], kind=r["kind"], source=r["source"],
                      timestamp=datetime.fromisoformat(r["timestamp"]),
                      status=EventStatus.CONFIRMED, confirmed_by_nurse=r["nurse"], note=r["note"])
            for r in rows
        ]
        return _latest_administration(events, dose_id)

    def record_event(self, dose_id: str, event: DoseEvent) -> None:
        require_confirmed(event)
        if event.dose_id != dose_id:
            raise ValueError(f"event is for {event.dose_id}, not {dose_id}")
        self._require_dose(dose_id)
        with self._conn:
            self._conn.execute(
                "INSERT INTO records (dose_id, kind, source, timestamp, nurse, note)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (dose_id, event.kind.value, event.source.value, event.timestamp.isoformat(),
                 event.confirmed_by_nurse, event.note),
            )


# -- CSV import -------------------------------------------------------------

CSV_COLUMNS = (
    "resident_id", "bed", "name", "preferred_language", "fallback_language",
    "dose_id", "medication", "dose", "date", "window_start", "window_end",
) + tuple(f"purpose_{language}" for language in MEDICATION_LANGUAGES)
_REQUIRED = ("resident_id", "bed", "name", "preferred_language", "dose_id",
             "medication", "dose", "window_start", "window_end")
OUTBOX_COLUMNS = ("dose_id", "kind", "source", "timestamp", "confirmed_by_nurse", "note")


@dataclass
class ImportReport:
    residents: int = 0
    doses: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)  # (line, why)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class _Plan:
    id: str
    resident_id: str
    medication: str
    dose: str
    day: date | None  # None: every day
    start: time
    end: time
    purpose: dict[str, str]


class CSVImportAdapter:
    """`path` is the eMAR export (one row per scheduled dose; a blank
    `date` means every day). `outbox` is where confirmed events go."""

    def __init__(self, path: str | Path, outbox: str | Path) -> None:
        self._outbox = Path(outbox)
        self.report, self._residents, self._plans = parse_export(Path(path))

    def list_residents(self) -> list[Resident]:
        return sorted(self._residents.values(), key=bed_order)

    def get_schedule(self, resident_id: str, day: date) -> list[ScheduledDose]:
        doses = []
        for plan in self._plans.values():
            if plan.resident_id != resident_id or (plan.day is not None and plan.day != day):
                continue
            doses.append(ScheduledDose(
                id=f"{plan.id}@{day.isoformat()}", resident_id=plan.resident_id,
                medication=plan.medication, dose=plan.dose,
                time_window=_window(day, plan.start, plan.end), purpose=dict(plan.purpose),
            ))
        return sorted(doses, key=lambda d: (d.time_window.start, d.id))

    def _require_dose(self, dose_id: str) -> None:
        plan_id, day = split_dose_id(dose_id)
        plan = self._plans.get(plan_id)
        if plan is None or (plan.day is not None and plan.day != day):
            raise UnknownDose(dose_id)

    def get_administration_status(self, dose_id: str) -> AdministrationStatus:
        self._require_dose(dose_id)
        return _latest_administration(
            (e for e in self._read_outbox() if e.dose_id == dose_id), dose_id
        )

    def record_event(self, dose_id: str, event: DoseEvent) -> None:
        require_confirmed(event)
        if event.dose_id != dose_id:
            raise ValueError(f"event is for {event.dose_id}, not {dose_id}")
        self._require_dose(dose_id)
        new = not self._outbox.exists()
        self._outbox.parent.mkdir(parents=True, exist_ok=True)
        with self._outbox.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(OUTBOX_COLUMNS)
            writer.writerow([dose_id, event.kind.value, event.source.value,
                             event.timestamp.isoformat(), event.confirmed_by_nurse,
                             event.note or ""])

    def _read_outbox(self) -> list[DoseEvent]:
        if not self._outbox.exists():
            return []
        with self._outbox.open(newline="", encoding="utf-8") as f:
            return [
                DoseEvent(dose_id=row["dose_id"], kind=row["kind"], source=row["source"],
                          timestamp=datetime.fromisoformat(row["timestamp"]),
                          status=EventStatus.CONFIRMED,
                          confirmed_by_nurse=row["confirmed_by_nurse"], note=row["note"] or None)
                for row in csv.DictReader(f)
            ]


def _time(value: str) -> time:
    return time.fromisoformat(value.strip().zfill(5))


def parse_export(path: Path) -> tuple[ImportReport, dict[str, Resident], dict[str, _Plan]]:
    """Read an eMAR export. Every bad row is in `report.errors` with its
    line number; the good rows are returned too, but callers that make
    an import live (`medication/cli.py`) refuse a file with errors."""
    report = ImportReport()
    residents: dict[str, Resident] = {}
    plans: dict[str, _Plan] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            report.errors.append((1, f"missing columns: {', '.join(missing)}"))
            return report, residents, plans
        for line, row in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in row.items() if k}
            blank = [c for c in _REQUIRED if not row.get(c)]
            if blank:
                report.errors.append((line, f"empty {', '.join(blank)}"))
                continue
            try:
                resident = Resident(
                    id=row["resident_id"], bed=row["bed"], name=row["name"],
                    preferred_language=row["preferred_language"],
                    fallback_language=row.get("fallback_language") or DEFAULT_FALLBACK_LANGUAGE,
                )
                day = date.fromisoformat(row["date"]) if row.get("date") else None
                start, end = _time(row["window_start"]), _time(row["window_end"])
                if end <= start:
                    raise ValueError("window_end is not after window_start")
            except (ValueError, UnknownLanguage) as exc:
                report.errors.append((line, str(exc)))
                continue
            purpose = {}
            bad_purpose = None
            for language in MEDICATION_LANGUAGES:
                text = row.get(f"purpose_{language}", "")
                if not text:
                    continue
                reason = dose_or_instruction(text)
                if reason:
                    what = "a dose" if reason == "dose" else "an instruction"
                    bad_purpose = f"purpose_{language} states {what}; purpose text may not"
                    break
                purpose[language] = text
            if bad_purpose:
                report.errors.append((line, bad_purpose))
                continue
            known = residents.get(resident.id)
            if known is not None and known != resident:
                report.errors.append((line, f"resident {resident.id} differs from an earlier row"))
                continue
            beds = {r.bed: r.id for r in residents.values()}
            if beds.get(resident.bed, resident.id) != resident.id:
                report.errors.append((line, f"bed {resident.bed} is already {beds[resident.bed]}"))
                continue
            if row["dose_id"] in plans or "@" in row["dose_id"]:
                why = f"dose_id {row['dose_id']!r} is repeated or contains '@'"
                report.errors.append((line, why))
                continue
            residents[resident.id] = resident
            plans[row["dose_id"]] = _Plan(
                id=row["dose_id"], resident_id=resident.id, medication=row["medication"],
                dose=row["dose"], day=day, start=start, end=end, purpose=purpose,
            )
    report.residents = len(residents)
    report.doses = len(plans)
    return report, residents, plans

