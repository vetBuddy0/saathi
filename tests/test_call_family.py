"""call/family.py -- pairing tokens are one-time and expire; member
keys authenticate and are never stored in the clear."""

from __future__ import annotations

import pytest

from saathi.call.family import PAIRING_TTL_SECONDS, FamilyRegistry, PairingError
from saathi.call.push import Subscription


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def registry(tmp_path, clock):
    return FamilyRegistry(tmp_path / "family.sqlite3", clock=clock)


def test_a_token_pairs_once(registry):
    token, _ = registry.issue_pairing_token()
    member, key = registry.redeem(token, "  Priya  ", "my daughter", "Mum")
    assert (member.name, member.relation, member.calls_her) == ("Priya", "daughter", "Mum")
    assert registry.authenticate(member.id, key) == member
    with pytest.raises(PairingError, match="already been used"):
        registry.redeem(token, "Someone else")


def test_a_token_expires(registry, clock):
    token, expires = registry.issue_pairing_token()
    assert expires == clock.now + PAIRING_TTL_SECONDS
    clock.now = expires
    with pytest.raises(PairingError, match="expired"):
        registry.redeem(token, "Priya")
    assert registry.members() == []


@pytest.mark.parametrize("token", ["", None, "made-up", 42])
def test_an_unknown_token_pairs_nobody(registry, token):
    with pytest.raises(PairingError):
        registry.redeem(token, "Priya")


@pytest.mark.parametrize("name", ["", "   ", "1234", None])
def test_a_name_is_required_and_the_token_survives_a_bad_one(registry, name):
    token, _ = registry.issue_pairing_token()
    with pytest.raises(PairingError):
        registry.redeem(token, name)
    member, _ = registry.redeem(token, "Priya")  # not spent by the failed attempt
    assert member.name == "Priya"


def test_a_wrong_key_or_a_revoked_member_is_not_authenticated(registry):
    token, _ = registry.issue_pairing_token()
    member, key = registry.redeem(token, "Priya")
    assert registry.authenticate(member.id, key + "x") is None
    assert registry.authenticate("nobody", key) is None
    assert registry.revoke(member.id)
    assert registry.authenticate(member.id, key) is None
    assert registry.members() == []


def test_secrets_are_stored_hashed(registry, tmp_path):
    token, _ = registry.issue_pairing_token()
    _, key = registry.redeem(token, "Priya")
    raw = (tmp_path / "family.sqlite3").read_bytes()
    assert key.encode() not in raw and token.encode() not in raw


def test_repairing_a_new_phone_replaces_the_old_one_for_calling(registry, clock):
    first, _ = registry.redeem(registry.issue_pairing_token()[0], "Priya", "daughter")
    clock.now += 60
    second, _ = registry.redeem(registry.issue_pairing_token()[0], "priya", "daughter")
    assert [m.id for m in registry.members()] == [second.id]
    assert registry.find_by_name("PRIYA").id == second.id
    assert registry.find_by_relation("my daughter").id == second.id


def test_a_subscription_is_kept_per_member(registry):
    member, _ = registry.redeem(registry.issue_pairing_token()[0], "Priya")
    sub = Subscription("https://push.test/x", b"\x04" + b"\x01" * 64, b"\x02" * 16)
    registry.set_subscription(member.id, sub)
    assert registry.get(member.id).subscription == sub
    registry.set_subscription(member.id, None)
    assert registry.get(member.id).subscription is None
