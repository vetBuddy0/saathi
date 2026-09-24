"""identity/profile.py — the family-visible view over what Saathi has
learned. list_rules()/describe_rule() fold in provenance; retract_rule()
is real since 2026-09-25 (IdentityStore.retire), where before it raised
RetractionNotSupported. The retraction test below changed with it --
the old one pinned "raises, and active stays 1", which was the
behaviour then and is not the behaviour now (see
docs/completed/memory.md, "Tests whose expectations changed").
"""

import tempfile
from pathlib import Path

import pytest

from saathi.identity.profile import describe_rule, list_rules, retract_rule
from saathi.identity.store import IdentityStore, UnknownRow


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def test_list_rules_on_an_empty_store_is_empty(store):
    assert list_rules(store) == []


def test_list_rules_includes_source_text_from_the_cited_episode(store):
    episode_id = store.append(
        "episodes", ts="2026-09-18T00:00:00+00:00", entity_id=None, text="Her scan is Thursday.",
        importance=7.0, embedding=None,
    )
    store.append(
        "rules", text="She has a scan Thursday.", confidence=0.9,
        learned_at="2026-09-18T00:00:00+00:00", source_episode=episode_id, active=1,
    )
    rules = list_rules(store)
    assert len(rules) == 1
    assert rules[0]["source_text"] == "Her scan is Thursday."


def test_list_rules_excludes_inactive_rules_by_default(store):
    store.append(
        "rules", text="active rule", confidence=0.9, learned_at="now",
        source_episode=None, active=1,
    )
    store.append(
        "rules", text="inactive rule", confidence=0.9, learned_at="now",
        source_episode=None, active=0,
    )
    rules = list_rules(store)
    assert [r["text"] for r in rules] == ["active rule"]


def test_list_rules_include_inactive_true_shows_everything(store):
    store.append(
        "rules", text="active rule", confidence=0.9, learned_at="now",
        source_episode=None, active=1,
    )
    store.append(
        "rules", text="inactive rule", confidence=0.9, learned_at="now",
        source_episode=None, active=0,
    )
    rules = list_rules(store, include_inactive=True)
    assert {r["text"] for r in rules} == {"active rule", "inactive rule"}


def test_list_rules_source_text_is_none_for_a_rule_with_no_source_episode(store):
    store.append(
        "rules", text="a rule with no provenance", confidence=0.9, learned_at="now",
        source_episode=None, active=1,
    )
    assert list_rules(store)[0]["source_text"] is None


def test_describe_rule_returns_none_for_an_unknown_id(store):
    assert describe_rule(store, 999) is None


def test_describe_rule_returns_the_rule_with_provenance(store):
    episode_id = store.append(
        "episodes", ts="now", entity_id=None, text="the source episode text",
        importance=5.0, embedding=None,
    )
    rule_id = store.append(
        "rules", text="the rule text", confidence=0.9, learned_at="now",
        source_episode=episode_id, active=1,
    )
    described = describe_rule(store, rule_id)
    assert described["text"] == "the rule text"
    assert described["source_text"] == "the source episode text"


def test_retract_rule_makes_the_rule_inactive_but_keeps_it_reviewable(store):
    rule_id = store.append(
        "rules", text="a rule", confidence=0.9, learned_at="now",
        source_episode=None, active=1,
    )
    retract_rule(store, rule_id)

    assert store.read("rules", id=rule_id)[0]["active"] == 0
    # Gone from what she hears, still visible to the family.
    assert list_rules(store) == []
    assert [r["id"] for r in list_rules(store, include_inactive=True)] == [rule_id]


def test_retract_rule_with_a_stale_id_raises_not_silently_succeeds(store):
    with pytest.raises(UnknownRow):
        retract_rule(store, 999)
