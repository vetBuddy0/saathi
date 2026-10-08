"""`saathi meds`: seed, import, show, assign -- through the real `saathi`
entry point, against a temporary data directory."""

import csv

import pytest

from saathi import cli
from saathi.medication.emar import CSV_COLUMNS
from saathi.medication.seed import RESIDENTS, ROUNDS
from saathi.medication.store import MedicationStore


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SAATHI_EMAR", raising=False)
    return tmp_path


def test_seed_then_show_a_bed(data_dir, capsys):
    assert cli.main(["meds", "seed"]) == 0
    assert "11 residents" in capsys.readouterr().out
    assert cli.main(["meds", "show", "12", "--date", "2026-10-08"]) == 0
    out = capsys.readouterr().out
    assert "Leung Kam Wah" in out
    assert "13:30-14:30  Paracetamol    1 g" in out and "not_logged" in out
    assert len([line for line in out.splitlines() if "not_logged" in line]) == len(ROUNDS["r09"])


def test_the_seed_is_eleven_residents_each_with_rounds():
    assert len(RESIDENTS) == 11 and len({r.bed for r in RESIDENTS}) == 11
    assert {r.preferred_language for r in RESIDENTS} == {"singlish", "cantonese", "hokkien",
                                                         "tamil"}
    assert all(r.fallback_language == "singlish" for r in RESIDENTS)
    assert all(ROUNDS[r.id] for r in RESIDENTS)


def test_assign_this_device_to_a_bed(data_dir, capsys):
    cli.main(["meds", "seed"])
    assert cli.main(["meds", "assign", "12", "--by", "nurse-ana"]) == 0
    assert MedicationStore(data_dir / "medication.sqlite3").assigned_resident() == "r09"
    cli.main(["meds", "residents"])
    assert "Leung Kam Wah" in [
        line for line in capsys.readouterr().out.splitlines() if "this device" in line
    ][0]
    assert cli.main(["meds", "assign", "99", "--by", "nurse-ana"]) == 1


def _export(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in CSV_COLUMNS})
    return path


ROW = dict(resident_id="a1", bed="3", name="Siti", preferred_language="tamil",
           dose_id="a1-1", medication="Amlodipine", dose="5 mg",
           window_start="08:00", window_end="09:00",
           purpose_singlish="This one is for your blood pressure.")


def test_a_good_import_goes_live(data_dir, tmp_path, capsys):
    path = _export(tmp_path / "export.csv", [ROW])
    assert cli.main(["meds", "import", str(path), "--by", "nurse-ana"]) == 0
    assert (data_dir / "emar_import.csv").exists()
    capsys.readouterr()
    cli.main(["meds", "show", "3", "--date", "2026-10-08"])
    assert "Amlodipine" in capsys.readouterr().out
    actions = [r["action"] for r in MedicationStore(data_dir / "medication.sqlite3").audit_log()]
    assert "emar_import" in actions


def test_a_bad_import_is_refused_and_says_why(data_dir, tmp_path, capsys):
    path = _export(tmp_path / "export.csv", [
        ROW, {**ROW, "dose_id": "a1-2", "purpose_singlish": "Take 2 tablets."}])
    assert cli.main(["meds", "import", str(path), "--by", "nurse-ana"]) == 1
    out = capsys.readouterr().out
    assert "line 3" in out and "states a dose" in out
    assert not (data_dir / "emar_import.csv").exists()


def test_residents_warns_when_the_assigned_resident_left_the_emar(data_dir, tmp_path, capsys):
    cli.main(["meds", "seed"])
    cli.main(["meds", "assign", "12", "--by", "nurse-ana"])
    cli.main(["meds", "import", str(_export(tmp_path / "export.csv", [ROW])), "--by", "nurse-ana"])
    capsys.readouterr()
    cli.main(["meds", "residents"])
    assert "isn't in the live eMAR" in capsys.readouterr().out
