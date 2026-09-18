"""identity/reflect.py — episodes -> rules with provenance, checkpoint 3.
No real Groq calls: a fake client scripts the two-step prompt (salient
questions, then grounded insights) the same way test_cascade.py's fakes
script chat completions.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from saathi.identity.reflect import reflect
from saathi.identity.store import IdentityStore

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def _add_episode(store, text, hours_ago=1, importance=5.0):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return store.append(
        "episodes", ts=ts, entity_id=None, text=text, importance=importance, embedding=None
    )


class FakeReflectionClient:
    """Scripts a fixed sequence of chat-completion responses, one per
    call -- the questions call first, then one insights call per
    question `reflect()` asks about."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._responses.pop(0) if self._responses else {}
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_reflect_on_an_empty_store_returns_nothing_and_calls_nothing(store):
    client = FakeReflectionClient([])
    assert reflect(store, client=client, now=NOW) == []
    assert client.calls == []


def test_reflect_writes_a_rule_with_provenance(store):
    episode_id = _add_episode(store, "She mentioned a scan on Thursday.", hours_ago=2)
    client = FakeReflectionClient(
        [
            {"questions": ["Is she anxious about an upcoming medical appointment?"]},
            {
                "insights": [
                    {"text": "She has a scan Thursday and seems anxious about it.", "cited": [1]}
                ]
            },
        ]
    )

    written = reflect(store, client=client, now=NOW)

    assert len(written) == 1
    rule = written[0]
    assert rule["text"] == "She has a scan Thursday and seems anxious about it."
    assert rule["source_episode"] == episode_id
    assert rule["active"] == 1
    assert rule["learned_at"] == NOW.isoformat()

    # Actually persisted, not just returned.
    stored_rules = store.read("rules")
    assert len(stored_rules) == 1


def test_reflect_confidence_scales_with_number_of_cited_episodes(store):
    _add_episode(store, "one", hours_ago=1)
    _add_episode(store, "two", hours_ago=2)
    _add_episode(store, "three", hours_ago=3)
    client = FakeReflectionClient(
        [
            {"questions": ["q"]},
            {"insights": [{"text": "grounded in all three", "cited": [1, 2, 3]}]},
        ]
    )
    written = reflect(store, client=client, now=NOW)
    assert written[0]["confidence"] == 1.0


def test_reflect_drops_an_insight_with_no_valid_citations(store):
    _add_episode(store, "one", hours_ago=1)
    client = FakeReflectionClient(
        [
            {"questions": ["q"]},
            {"insights": [{"text": "an unsupported guess", "cited": []}]},
        ]
    )
    assert reflect(store, client=client, now=NOW) == []
    assert store.read("rules") == []


def test_reflect_drops_a_citation_index_out_of_range(store):
    _add_episode(store, "one", hours_ago=1)
    client = FakeReflectionClient(
        [
            {"questions": ["q"]},
            # Statement 9 was never offered -- must not be trusted as
            # real provenance.
            {"insights": [{"text": "fabricated citation", "cited": [9]}]},
        ]
    )
    assert reflect(store, client=client, now=NOW) == []


def test_reflect_survives_a_malformed_questions_response(store):
    _add_episode(store, "one", hours_ago=1)
    client = FakeReflectionClient([{"not_questions_at_all": True}])
    assert reflect(store, client=client, now=NOW) == []


def test_reflect_survives_a_malformed_insights_response(store):
    _add_episode(store, "one", hours_ago=1)
    client = FakeReflectionClient([{"questions": ["q"]}, {"unexpected": "shape"}])
    assert reflect(store, client=client, now=NOW) == []


def test_reflect_asks_one_insights_call_per_proposed_question(store):
    _add_episode(store, "one", hours_ago=1)
    client = FakeReflectionClient(
        [
            {"questions": ["q1", "q2"]},
            {"insights": []},
            {"insights": []},
        ]
    )
    reflect(store, client=client, now=NOW)
    assert len(client.calls) == 3  # 1 questions call + 2 insights calls
