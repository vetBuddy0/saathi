"""identity/test_profile.py and `saathi test-resident`: Lee Kim Tan's
profile lands as sourced rules, his son is found by relation, nothing
about doses reaches the identity file, and a second run writes nothing."""

import pytest

from saathi import cli
from saathi.call.contacts import find_by_relation
from saathi.identity.store import IdentityStore
from saathi.identity.test_profile import ADMISSION, RULES, SON_PHONE, apply_test_profile
from saathi.medication.guard import dose_or_instruction
from saathi.medication.store import MedicationStore


def test_profile_rules_trace_to_the_admission_episode(tmp_path):
    path = tmp_path / "identity.sqlite3"
    applied = apply_test_profile(path)
    with IdentityStore(path) as store:
        [episode] = [e for e in store.read("episodes") if e["text"] == ADMISSION]
        rules = store.read("rules")
    assert [r["text"] for r in rules] == list(RULES)
    assert {r["source_episode"] for r in rules} == {episode["id"]} == {applied.episode_id}
    son = find_by_relation(path, "son")
    assert son.phone == SON_PHONE and son.name == "Udhi"


def test_a_second_run_writes_nothing(tmp_path):
    path = tmp_path / "identity.sqlite3"
    apply_test_profile(path)
    again = apply_test_profile(path)
    assert again.rules_written == 0 and not again.son_saved
    with IdentityStore(path) as store:
        assert len(store.read("rules")) == len(RULES)


def test_the_profile_states_no_dose():
    # His medicines live in the medication store, never in what the model reads.
    for text in (ADMISSION, *RULES):
        assert dose_or_instruction(text) in (None, "instruction"), text
    assert all("mg" not in text for text in RULES)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SAATHI_IDENTITY_DB", raising=False)
    monkeypatch.delenv("SAATHI_EMAR", raising=False)
    return tmp_path


def test_test_resident_sets_up_the_device(data_dir, capsys):
    assert cli.main(["test-resident", "--by", "nurse-ana"]) == 0
    assert MedicationStore(data_dir / "medication.sqlite3").assigned_resident() == "r11"
    capsys.readouterr()
    cli.main(["meds", "show", "6", "--date", "2026-10-08"])
    out = capsys.readouterr().out
    assert "Lee Kim Tan" in out and "Tamsulosin" in out and "Allopurinol" in out
    assert out.count("not_logged") == 10
    assert cli.main(["test-resident", "--by", "nurse-ana"]) == 0
    assert "nothing rewritten" in capsys.readouterr().out
