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


def test_a_damaged_or_mismatched_embedding_scores_zero_rather_than_raising():
    # compile_context runs on CascadeSession's constructor thread: one
    # bad row (a write cut short, or a row from a different-dimension
    # model after the files were swapped) must not stop a session.
    query = np.array([1.0, 0.0], dtype=np.float32)
    episodes = [
        {"ts": NOW.isoformat(), "text": "truncated", "importance": 1.0, "embedding": b"\x00\x00"},
        {"ts": NOW.isoformat(), "text": "empty", "importance": 1.0, "embedding": b""},
        {
            "ts": NOW.isoformat(),
            "text": "wrong dimension",
            "importance": 1.0,
            "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes(),
        },
        {"ts": NOW.isoformat(), "text": "good", "importance": 1.0, "embedding": query.tobytes()},
    ]
    top = retrieve_episodes(episodes, now=NOW, query_embedding=query, top_k=1)
    assert top[0]["text"] == "good"


def test_compile_context_survives_a_damaged_newest_embedding(store, base_persona_path):
    store.append(
        "episodes", ts=NOW.isoformat(), entity_id=None, text="Fine row.", importance=5.0,
        embedding=np.array([1.0, 0.0], dtype=np.float32).tobytes(),
    )
    store.append(
        "episodes", ts=(NOW + timedelta(0, 1)).isoformat(), entity_id=None, text="Cut short.",
        importance=5.0, embedding=b"\x01\x02\x03",
    )
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert "Fine row." in context and "Cut short." in context


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


def test_compile_context_numbers_each_rule_by_its_id_for_the_correction_tool(
    store, base_persona_path
):
    # The one piece of storage that reaches the prompt on purpose: the
    # model must be able to say *which* belief she said was wrong, and
    # cascade honours one tool call per turn, so a list-then-retire
    # two-step can't happen. See compile.py's module docstring.
    first = store.append(
        "rules", text="She takes her tablets at eight.", confidence=0.9,
        learned_at=NOW.isoformat(), active=1,
    )
    second = store.append(
        "rules", text="Her daughter visits on Saturdays.", confidence=0.9,
        learned_at=NOW.isoformat(), active=1,
    )
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert f"[memory {first}] She takes her tablets at eight." in context
    assert f"[memory {second}] Her daughter visits on Saturdays." in context
    assert "never say a number aloud" in context


def test_compile_context_without_rules_sends_no_memory_number_instructions(
    store, base_persona_path
):
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert "[memory" not in context
    assert "memory number" not in context


def _embedded_episode(store, text, vector, ts=NOW):
    return store.append(
        "episodes", ts=ts.isoformat(), entity_id=None, text=text, importance=5.0,
        embedding=np.array(vector, dtype=np.float32).tobytes(),
    )


def test_compile_context_uses_the_newest_episodes_embedding_as_the_query(
    store, base_persona_path
):
    # No caller passes a query embedding today; "relevant to what was
    # just discussed" is the newest episode's own vector. Three episodes
    # of equal recency and importance; with top_k=2 the newest and the
    # one nearest to it are sent, and the unrelated one is not.
    _embedded_episode(store, "She likes milky tea.", [1.0, 0.0, 0.0])
    _embedded_episode(store, "The hospital appointment is at ten.", [0.0, 1.0, 0.0])
    _embedded_episode(store, "Her scan is on Thursday.", [0.0, 0.9, 0.1], ts=NOW + timedelta(0, 1))
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path, top_k=2)
    assert "Her scan is on Thursday." in context
    assert "The hospital appointment is at ten." in context
    assert "milky tea" not in context


def test_an_explicit_query_embedding_overrides_the_newest_episode(store, base_persona_path):
    # Same timestamp for all three so recency is flat and only the
    # query decides. The newest (by id) is the scan; the caller asks
    # about tea.
    _embedded_episode(store, "She likes milky tea.", [1.0, 0.0, 0.0])
    _embedded_episode(store, "The hospital appointment is at ten.", [0.0, 1.0, 0.0])
    _embedded_episode(store, "Her scan is on Thursday.", [0.0, 0.9, 0.1])
    context = compile_context(
        store, now=NOW, base_persona_path=base_persona_path, top_k=1,
        query_embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32),
    )
    assert "milky tea" in context
    assert "hospital appointment" not in context


def test_compile_context_with_a_newest_episode_lacking_an_embedding_degrades_to_no_query(
    store, base_persona_path
):
    _embedded_episode(store, "She likes milky tea.", [1.0, 0.0, 0.0])
    store.append(
        "episodes", ts=(NOW + timedelta(0, 1)).isoformat(), entity_id=None,
        text="Something with no vector yet.", importance=5.0, embedding=None,
    )
    # Must not raise, and both are still sent -- ranking on recency +
    # importance alone, as before embeddings existed.
    context = compile_context(store, now=NOW, base_persona_path=base_persona_path)
    assert "milky tea" in context
    assert "no vector yet" in context
