"""cloud/pairing.py: an 8-character code at pairing/{code} for this device,
live 10 minutes; a collision is tried again with another code; who has
paired is read from devices/{id}."""

from datetime import datetime, timedelta, timezone

import pytest

from saathi.cloud import pairing
from tests.fake_firestore import AlreadyExists, FakeFirestore

NOW = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)


# Was six digits: the owner chose (2026-10-29) a code too large to guess
# within its 10 minutes, since rules can't count attempts (DECISIONS.md).
def test_a_new_code_is_eight_unambiguous_characters_for_this_device_and_lives_ten_minutes():
    client = FakeFirestore()
    code, expires_at = pairing.new_code(client, "dev1", NOW)
    assert len(code) == 8 and set(code) <= set(pairing.CODE_ALPHABET)
    assert expires_at == NOW + timedelta(minutes=10)
    assert client.docs[f"pairing/{code}"] == {"deviceId": "dev1", "expiresAt": expires_at}


def test_the_alphabet_has_no_look_alikes_and_codes_are_drawn_from_secrets(monkeypatch):
    assert not set("01ILOU") & set(pairing.CODE_ALPHABET)
    assert len(set(pairing.CODE_ALPHABET)) == len(pairing.CODE_ALPHABET) == 30
    monkeypatch.setattr(pairing.secrets, "choice", lambda alphabet: alphabet[-1])
    code, _ = pairing.new_code(FakeFirestore(), "dev1", NOW)
    assert code == "ZZZZZZZZ"


def test_a_collision_raises_and_issue_code_tries_another(monkeypatch):
    client = FakeFirestore()
    client.put("pairing/22222222", deviceId="other", expiresAt=NOW)
    picks = iter("2" * 8 + "2" * 8 + "3" * 8)
    monkeypatch.setattr(pairing.secrets, "choice", lambda _alphabet: next(picks))
    with pytest.raises(AlreadyExists):
        pairing.new_code(client, "dev1", NOW)
    code, _ = pairing.issue_code(client, "dev1", NOW)
    assert code == "33333333"
    assert client.docs["pairing/22222222"]["deviceId"] == "other"  # untouched


def test_issue_code_gives_up_after_its_tries():
    client = FakeFirestore()
    client.offline = True
    with pytest.raises(Exception, match="no network"):
        pairing.issue_code(client, "dev1", NOW, attempts=2)
    assert [c[0] for c in client.calls] == ["create_doc", "create_doc"]


def test_paired_caregivers_are_read_from_the_device_doc():
    client = FakeFirestore()
    assert pairing.paired_caregivers(client, "dev1") == []
    client.put("devices/dev1", name="Saathi", caregivers=["uidA", "uidB"])
    assert pairing.paired_caregivers(client, "dev1") == ["uidA", "uidB"]


def test_the_device_doc_is_made_once_for_caregivers_to_join():
    client = FakeFirestore()
    doc = pairing.ensure_device(client, "dev1", NOW, name="Saathi", device_uid="u-dev")
    assert client.docs["devices/dev1"] == {
        "name": "Saathi", "createdAt": NOW, "caregivers": [], "deviceUid": "u-dev",
    }
    assert doc["id"] == "dev1"
    client.docs["devices/dev1"]["caregivers"] = ["uidA"]
    again = pairing.ensure_device(client, "dev1", NOW + timedelta(days=1))
    assert again["caregivers"] == ["uidA"]  # never overwritten
