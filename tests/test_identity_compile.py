"""identity/compile.py — the retrieval-scoring math (Generative Agents:
recency + importance + relevance, each normalized before summing) and
compile_context()'s degrade-sensibly-when-empty behavior, against a real
(temp-file) IdentityStore. `cascade.py`'s use of this (compiled between
turns, not during one) is covered separately in tests/test_cascade.py.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from saathi.identity.compile import compile_context, retrieve_episodes
from saathi.identity.store import IdentityStore


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


@pytest.fixture
def base_persona_path(tmp_path):
    path = tmp_path / "persona_stub.txt"
    path.write_text("You are a warm companion.")
    return path


NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def _episode(text, hours_ago, importance):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"ts": ts, "text": text, "importance": importance, "embedding": None}


# -- retrieve_episodes() --------------------------------------------------


def test_retrieve_episodes_on_empty_list_returns_empty():
    assert retrieve_episodes([], now=NOW) == []


def test_retrieve_episodes_ranks_a_recent_low_importance_episode_over_an_old_one():
    episodes = [
        _episode("old news", hours_ago=1000, importance=1.0),
        _episode("fresh news", hours_ago=0.1, importance=1.0),
    ]
    top = retrieve_episodes(episodes, now=NOW, top_k=1)
    assert top[0]["text"] == "fresh news"


def test_retrieve_episodes_ranks_a_high_importance_episode_over_a_low_one_at_equal_recency():
    episodes = [
        _episode("trivial", hours_ago=1.0, importance=0.0),
        _episode("she has a scan Thursday and is anxious", hours_ago=1.0, importance=10.0),
    ]
    top = retrieve_episodes(episodes, now=NOW, top_k=1)
    assert "scan Thursday" in top[0]["text"]


def test_retrieve_episodes_respects_top_k():
    episodes = [_episode(f"episode {i}", hours_ago=i, importance=1.0) for i in range(10)]
    assert len(retrieve_episodes(episodes, now=NOW, top_k=3)) == 3


def test_retrieve_episodes_with_no_query_embedding_ignores_relevance_not_errors():
    episodes = [_episode("a", hours_ago=1, importance=1.0)]
    # Must not raise despite every episode having embedding=None and no
    # query_embedding being given -- see the module docstring.
    result = retrieve_episodes(episodes, now=NOW, query_embedding=None)
    assert len(result) == 1


def test_retrieve_episodes_uses_relevance_when_a_query_embedding_and_real_embeddings_exist():
    matching = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    orthogonal = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    episodes = [
        {
            "ts": NOW.isoformat(),
            "text": "unrelated",
            "importance": 1.0,
            "embedding": orthogonal.tobytes(),
        },
        {
            "ts": NOW.isoformat(),
            "text": "relevant",
            "importance": 1.0,
            "embedding": matching.tobytes(),
        },
    ]
    top = retrieve_episodes(episodes, now=NOW, query_embedding=matching, top_k=1)
    assert top[0]["text"] == "relevant"


def test_retrieve_episodes_missing_embedding_on_one_episode_still_ranks_the_rest():
    matching = np.array([1.0, 0.0], dtype=np.float32)
    episodes = [
        {"ts": NOW.isoformat(), "text": "no embedding", "importance": 1.0, "embedding": None},
        {
            "ts": NOW.isoformat(),
            "text": "has embedding",
            "importance": 1.0,
            "embedding": matching.tobytes(),
        },
    ]
    top = retrieve_episodes(episodes, now=NOW, query_embedding=matching, top_k=1)
    assert top[0]["text"] == "has embedding"


# -- compile_context() -----------------------------------------------------


def test_compile_context_on_an_empty_store_is_just_the_base_persona_and_length_cap(
    store, base_persona_path
):
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert base_persona_path.read_text().strip() in context
    assert "two sentences" in context
    # Nothing fabricated for an empty store -- no episode or rule text
    # appears because none exist.
    assert "scan" not in context


def test_compile_context_includes_active_rules_as_plain_sentences(store, base_persona_path):
    store.append(
        "rules",
        text="She likes being greeted by name.",
        confidence=0.9,
        learned_at=NOW.isoformat(),
        active=1,
    )
    store.append(
        "rules",
        text="This rule was retracted.",
        confidence=0.9,
        learned_at=NOW.isoformat(),
        active=0,
    )
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert "She likes being greeted by name." in context
    assert "This rule was retracted." not in context


def test_compile_context_includes_retrieved_episode_text(store, base_persona_path):
    store.append(
        "episodes",
        ts=NOW.isoformat(),
        entity_id=None,
        text="Her daughter visited on Saturday.",
        importance=8.0,
        embedding=None,
    )
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert "Her daughter visited on Saturday." in context


def test_compile_context_never_leaks_raw_confidence_or_timestamps_into_the_text(
    store, base_persona_path
):
    # "Stored and sent are different" (SPEC.md) -- the compiled context
    # is plain sentences, not the rows themselves.
    store.append(
        "rules",
        text="She prefers tea over coffee.",
        confidence=0.73,
        learned_at=NOW.isoformat(),
        active=1,
    )
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert "0.73" not in context
    assert NOW.isoformat() not in context
