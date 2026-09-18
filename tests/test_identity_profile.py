"""identity/profile.py — the family-visible view over what Saathi has
learned. list_rules()/describe_rule() are real; retract_rule() is a
real, documented gap (RetractionNotSupported), not a silent no-op.
"""

import tempfile
from pathlib import Path

import pytest

from saathi.identity.profile import RetractionNotSupported, describe_rule, list_rules, retract_rule
from saathi.identity.store import IdentityStore


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


def test_retract_rule_raises_retraction_not_supported(store):
    rule_id = store.append(
        "rules", text="a rule", confidence=0.9, learned_at="now",
        source_episode=None, active=1,
    )
    with pytest.raises(RetractionNotSupported) as exc_info:
        retract_rule(store, rule_id)
    assert exc_info.value.rule_id == rule_id

    # And, crucially, nothing was silently changed.
    rules = store.read("rules", id=rule_id)
    assert rules[0]["active"] == 1
