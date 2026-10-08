"""`saathi meds`: the staff's shell view of the medication records until
the dashboard exists (Phase 2), and how a device is assigned to its
resident.

Why the eMAR is chosen here and not in config.py: which eMAR is live is
a fact about the data directory -- an accepted import makes the CSV
live (`emar_import.csv` there), otherwise the seeded mock --
with `SAATHI_EMAR=mock|csv` to force one. Lost: a required env var,
which a staff member uploading an export would have to know to set.

Everything here is for staff eyes: `show` prints doses. Nothing here
speaks, and nothing here writes to the eMAR (only writer.py does).
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import date, datetime
from pathlib import Path

from saathi.medication.emar import CSVImportAdapter, EMARAdapter, MockEMARAdapter, parse_export
from saathi.medication.store import MedicationStore

IMPORT_NAME = "emar_import.csv"
OUTBOX_NAME = "emar_outbox.csv"
MOCK_NAME = "mock_emar.sqlite3"
STORE_NAME = "medication.sqlite3"


def open_emar(data_dir: Path) -> EMARAdapter:
    choice = os.environ.get("SAATHI_EMAR", "").strip().lower()
    imported = data_dir / IMPORT_NAME
    if choice == "csv" or (choice != "mock" and imported.exists()):
        return CSVImportAdapter(imported, data_dir / OUTBOX_NAME)
    return MockEMARAdapter(data_dir / MOCK_NAME)


def open_store(data_dir: Path) -> MedicationStore:
    return MedicationStore(data_dir / STORE_NAME)


def add_parser(subparsers) -> None:
    meds = subparsers.add_parser("meds", help="medication records (staff)")
    sub = meds.add_subparsers(dest="meds_command", required=True)
    sub.add_parser("seed", help="fill the mock eMAR with the fake ward")
    imp = sub.add_parser("import", help="check an eMAR export and make it live")
    imp.add_argument("csv", type=Path)
    imp.add_argument("--by", required=True, help="who is uploading it")
    show = sub.add_parser("show", help="a bed's schedule for a day, with what's logged")
    show.add_argument("bed")
    show.add_argument("--date", type=date.fromisoformat, default=None)
    assign = sub.add_parser("assign", help="assign this device to a resident's bed")
    assign.add_argument("bed")
    assign.add_argument("--by", required=True, help="who is assigning it")
    sub.add_parser("residents", help="list residents, and which one this device is for")


def run(args: argparse.Namespace, data_dir: Path) -> int:
    command = args.meds_command
    store = open_store(data_dir)
    if command == "seed":
        from saathi.medication.seed import RESIDENTS, seed

        count = seed(MockEMARAdapter(data_dir / MOCK_NAME))
        store.audit("cli", "mock_seeded", None, doses=count)
        print(f"Mock eMAR seeded: {len(RESIDENTS)} residents, {count} daily doses.")
        if (data_dir / IMPORT_NAME).exists() and os.environ.get("SAATHI_EMAR") != "mock":
            print(f"Note: an import is live ({IMPORT_NAME}); SAATHI_EMAR=mock uses the mock.")
        return 0

    if command == "import":
        report, _residents, _plans = parse_export(args.csv)
        if not report.ok:
            print(f"Not imported: {len(report.errors)} row(s) need fixing first.")
            for line, why in report.errors:
                print(f"  line {line}: {why}")
            store.audit(args.by, "emar_import_rejected", str(args.csv),
                        errors=report.errors)
            return 1
        shutil.copyfile(args.csv, data_dir / IMPORT_NAME)
        store.audit(args.by, "emar_import", str(args.csv),
                    residents=report.residents, doses=report.doses)
        print(f"Imported {report.residents} residents, {report.doses} scheduled doses.")
        return 0

    emar = open_emar(data_dir)
    residents = {r.bed: r for r in emar.list_residents()}

    if command == "residents":
        assigned = store.assigned_resident()
        for resident in residents.values():
            mark = "  <- this device" if resident.id == assigned else ""
            print(f"Bed {resident.bed:>3}  {resident.name:<20} "
                  f"{resident.preferred_language} (fallback {resident.fallback_language}){mark}")
        if assigned is not None and assigned not in {r.id for r in residents.values()}:
            print(f"This device is assigned to {assigned}, who isn't in the live eMAR;"
                  " assign it again: saathi meds assign <bed> --by <you>")
        elif assigned is None:
            print("This device isn't assigned to a resident yet:"
                  " saathi meds assign <bed> --by <you>")
        return 0

    resident = residents.get(args.bed)
    if resident is None:
        print(f"No resident in bed {args.bed}.")
        return 1

    if command == "assign":
        store.assign(resident.id, by=args.by)
        print(f"This device is now for bed {resident.bed}, {resident.name}.")
        return 0

    if command == "show":
        day = args.date or datetime.now().date()
        print(f"Bed {resident.bed}, {resident.name} -- {day.isoformat()}")
        for dose in emar.get_schedule(resident.id, day):
            status = emar.get_administration_status(dose.id)
            window = (f"{dose.time_window.start:%H:%M}-{dose.time_window.end:%H:%M}")
            who = f" by {status.event.confirmed_by_nurse}" if status.event else ""
            pending = len([e for e in store.events(dose.id) if e.status.value == "pending"])
            waiting = f"  ({pending} waiting for a nurse)" if pending else ""
            print(f"  {window}  {dose.medication:<14} {dose.dose:<8} {status.state}{who}{waiting}")
        return 0

    return 1
