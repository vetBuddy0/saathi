"""saathi/call/contacts.py — people are entities, relationships are
edges, latest wins; no new table."""

import json
import sqlite3
import threading

import pytest

from saathi.call import contacts
from saathi.identity.store import IdentityStore


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "identity.sqlite3"
    with IdentityStore(p) as store:
        store.create()
    return p


def test_a_contact_is_a_person_entity_plus_an_edge_from_self(path):
    person_id = contacts.save_contact(path, "Priya", ("+" + "6591234567"), "SG", "my daughter")
    with IdentityStore(path) as store:
        people = store.read("entities", kind="person")
        selves = store.read("entities", kind="self")
        edges = store.read("edges")
    assert len(selves) == 1 and people[0]["id"] == person_id
    assert json.loads(people[0]["notes"]) == {"phone": ("+" + "6591234567"), "country": "SG"}
    assert edges == [
        {"src": selves[0]["id"], "dst": person_id, "relation": "daughter",
         "since": edges[0]["since"], "until": None}
    ]


def test_no_contacts_table_exists(path):
    contacts.save_contact(path, "Priya", ("+" + "6591234567"), "SG", None)
    tables = {r[0] for r in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any("contact" in t for t in tables)


def test_call_my_daughter_resolves_through_the_graph(path):
    contacts.save_contact(path, "Priya", ("+" + "6591234567"), "SG", "daughter")
    contacts.save_contact(path, "Ravi", ("+" + "6598765432"), "SG", "son")
    assert contacts.find_by_relation(path, "my daughter").name == "Priya"
    assert contacts.find_by_relation(path, "son").phone == ("+" + "6598765432")
    assert contacts.find_by_relation(path, "sister") is None


def test_self_row_is_created_once(path):
    contacts.save_contact(path, "A", ("+" + "6591111111"), "SG", "friend")
    contacts.save_contact(path, "B", ("+" + "6592222222"), "SG", "neighbour")
    with IdentityStore(path) as store:
        assert len(store.read("entities", kind="self")) == 1


def test_a_corrected_number_supersedes_by_latest_wins(path):
    contacts.save_contact(path, "Priya", ("+" + "6591234567"), "SG", "daughter")
    contacts.save_contact(path, "priya", ("+" + "6590000000"), "SG", None)
    found = contacts.find_by_exact_name(path, "PRIYA")
    assert found.phone == ("+" + "6590000000")
    # The relation still reaches her through the name, to the new number.
    assert contacts.find_by_relation(path, "daughter").phone == ("+" + "6590000000")
    assert len(contacts.list_contacts(path)) == 1
    with IdentityStore(path) as store:
        assert len(store.read("entities", kind="person")) == 2  # history kept


def test_a_new_daughter_edge_wins_over_the_old_one(path):
    contacts.save_contact(path, "Priya", ("+" + "6591234567"), "SG", "daughter")
    contacts.save_contact(path, "Meena", ("+" + "6593333333"), "SG", "daughter")
    assert contacts.find_by_relation(path, "daughter").name == "Meena"


def test_names_match_without_accents_or_case(path):
    contacts.save_contact(path, "José", ("+" + "6591234567"), "SG", None)
    assert contacts.find_by_exact_name(path, "jose").name == "José"


def test_relation_normalisation():
    assert contacts.normalise_relation("My Mum") == "mother"
    assert contacts.normalise_relation("daughter's") == "daughter"
    assert contacts.normalise_relation("daughter-in-law") == "daughter-in-law"
    assert contacts.normalise_relation("my physio") == "physio"
    assert contacts.is_known_relation("my daughter")
    assert not contacts.is_known_relation("Priya")


def test_reads_and_writes_work_from_another_thread(path):
    results = {}

    def work():
        contacts.save_contact(path, "Priya", ("+" + "6591234567"), "SG", "daughter")
        results["found"] = contacts.find_by_relation(path, "daughter")

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(5)
    assert results["found"].name == "Priya"


def test_malformed_notes_are_skipped_not_crashed_on(path):
    with IdentityStore(path) as store:
        store.append("entities", kind="person", name="Odd", notes="not json", created_at="x")
    assert contacts.list_contacts(path) == []
