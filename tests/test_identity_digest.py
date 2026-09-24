"""identity/digest.py: one background model call -> an episodes row
and a re-folded summary. The model is a fake returning scripted JSON;
the store is a real IdentityStore on a temp file.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from saathi.identity.digest import TurnDigest, digest_turn, write_episode
from saathi.identity.store import IdentityStore
from saathi.voice.conversation import Exchange


class FakeChat:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _client(content: str) -> SimpleNamespace:
    chat = FakeChat(content)
    return SimpleNamespace(chat=SimpleNamespace(completions=chat))


def _tmp_store() -> IdentityStore:
    path = Path(tempfile.mkdtemp()) / "identity.sqlite3"
    store = IdentityStore(path)
    store.create()
    return store


EXCHANGE = Exchange(user="the scan is on Thursday", assistant="I'll remember Thursday.")


def test_digest_parses_observation_importance_and_summary():
    client = _client(
        json.dumps(
            {
                "observation": "She mentioned her scan is on Thursday.",
                "importance": 7,
                "summary": "They discussed an upcoming scan on Thursday.",
            }
        )
    )
    digest = digest_turn(client, model="m", exchange=EXCHANGE, unfolded=[], summary="")

    assert digest == TurnDigest(
        observation="She mentioned her scan is on Thursday.",
        importance=7.0,
        summary="They discussed an upcoming scan on Thursday.",
    )
    call = client.chat.completions.calls[0]
    assert call["model"] == "m"
    assert call["response_format"] == {"type": "json_object"}
    prompt = call["messages"][0]["content"]
    assert "the scan is on Thursday" in prompt
    assert "I'll remember Thursday." in prompt


def test_unfolded_exchanges_and_existing_summary_reach_the_prompt():
    client = _client(json.dumps({"observation": None, "importance": None, "summary": "s2"}))
    unfolded = [Exchange(user="older question", assistant="older answer")]
    digest_turn(client, model="m", exchange=EXCHANGE, unfolded=unfolded, summary="s1")

    prompt = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "Existing summary: s1" in prompt
    assert "older question" in prompt and "older answer" in prompt


def test_null_observation_is_a_real_answer_not_a_failure():
    # Small talk: the model says there's nothing to remember. Summary
    # still comes back so layer 2 advances; importance is dropped with
    # the observation rather than carried for a memory that isn't written.
    client = _client(json.dumps({"observation": None, "importance": 3, "summary": "chit-chat"}))
    digest = digest_turn(client, model="m", exchange=EXCHANGE, unfolded=[], summary="")
    assert digest == TurnDigest(observation=None, importance=None, summary="chit-chat")


def test_malformed_json_yields_an_all_none_digest_and_never_raises():
    client = _client("Sure! Here's what I remember: ...")
    digest = digest_turn(client, model="m", exchange=EXCHANGE, unfolded=[], summary="prior")
    assert digest == TurnDigest(observation=None, importance=None, summary=None)


def test_non_object_json_is_treated_as_malformed():
    client = _client(json.dumps(["not", "an", "object"]))
    digest = digest_turn(client, model="m", exchange=EXCHANGE, unfolded=[], summary="")
    assert digest == TurnDigest(observation=None, importance=None, summary=None)


def test_importance_is_clamped_to_the_1_to_10_scale_not_rejected():
    high = _client(json.dumps({"observation": "x", "importance": 42, "summary": "s"}))
    low = _client(json.dumps({"observation": "x", "importance": -5, "summary": "s"}))
    junk = _client(json.dumps({"observation": "x", "importance": "very", "summary": "s"}))

    assert (
        digest_turn(high, model="m", exchange=EXCHANGE, unfolded=[], summary="").importance == 10.0
    )
    assert digest_turn(low, model="m", exchange=EXCHANGE, unfolded=[], summary="").importance == 1.0
    assert (
        digest_turn(junk, model="m", exchange=EXCHANGE, unfolded=[], summary="").importance is None
    )


def test_write_episode_appends_a_row_with_entity_left_null():
    # Changed 2026-09-25: this used to also pin `embedding is None` as
    # a deliberate gap. Embeddings are real now (identity/embed.py);
    # with no model on the machine the default embedder yields None,
    # which is what CI sees and what tests/conftest.py guarantees here.
    store = _tmp_store()
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    row_id = write_episode(store, "She mentioned her scan is on Thursday.", 7.0, now=now)

    rows = store.read("episodes")
    store.close()
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["text"] == "She mentioned her scan is on Thursday."
    assert rows[0]["importance"] == 7.0
    assert rows[0]["ts"] == now.isoformat()
    # Not an oversight -- see write_episode's docstring.
    assert rows[0]["entity_id"] is None
    # No model files in the test environment: degraded, not broken.
    assert rows[0]["embedding"] is None


def _fake_embedder(text: str) -> np.ndarray:
    # 4-dim, deterministic: one bucket per topic word, so tests can
    # reason about which sentence is "near" which without a model.
    vector = np.zeros(4, dtype=np.float32)
    for i, word in enumerate(("scan", "hospital", "tea", "garden")):
        if word in text.lower():
            vector[i] = 1.0
    return vector


def test_write_episode_stores_the_embedder_output_as_float32_bytes():
    store = _tmp_store()
    write_episode(store, "her scan is on Thursday", 5.0, embedder=_fake_embedder)
    blob = store.read("episodes")[0]["embedding"]
    store.close()
    assert isinstance(blob, bytes)
    assert len(blob) == 4 * 4
    np.testing.assert_array_equal(np.frombuffer(blob, dtype=np.float32), [1.0, 0.0, 0.0, 0.0])


def test_write_episode_with_an_explicit_embedding_does_not_call_the_embedder():
    calls = []

    def _embedder(text):
        calls.append(text)
        return _fake_embedder(text)

    store = _tmp_store()
    given = np.array([0.5, 0.5, 0.0, 0.0], dtype=np.float32).tobytes()
    write_episode(store, "anything", 5.0, embedding=given, embedder=_embedder)
    assert store.read("episodes")[0]["embedding"] == given
    store.close()
    assert calls == []


def test_write_episode_with_embedder_none_writes_null_on_purpose():
    # The correction tool's path: inside a turn, no model may load.
    store = _tmp_store()
    write_episode(store, "anything", 5.0, embedder=None)
    assert store.read("episodes")[0]["embedding"] is None
    store.close()


def test_write_episode_when_the_embedder_yields_none_writes_null():
    store = _tmp_store()
    write_episode(store, "anything", 5.0, embedder=lambda _text: None)
    assert store.read("episodes")[0]["embedding"] is None
    store.close()


def test_stored_embeddings_make_relevance_reorder_retrieval():
    # The reason embeddings exist: two episodes of equal recency and
    # importance, and the one about the same topic as the query wins.
    from saathi.identity.compile import retrieve_episodes

    store = _tmp_store()
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    write_episode(store, "She likes milky tea.", 5.0, now=now, embedder=_fake_embedder)
    write_episode(store, "Her hospital scan is Thursday.", 5.0, now=now, embedder=_fake_embedder)
    episodes = store.read("episodes")
    store.close()

    top = retrieve_episodes(episodes, now=now, query_embedding=_fake_embedder("the scan"), top_k=1)
    assert "scan" in top[0]["text"]
    top = retrieve_episodes(episodes, now=now, query_embedding=_fake_embedder("her tea"), top_k=1)
    assert "tea" in top[0]["text"]


def test_a_written_episode_reaches_compiled_context_as_a_plain_sentence():
    # Layer 3 end to end: the row this module writes is what
    # compile_context() sends -- text only, no importance float leaking.
    from saathi.identity.compile import compile_context

    store = _tmp_store()
    write_episode(store, "She mentioned her scan is on Thursday.", 7.0)
    context = compile_context(store)
    store.close()
    assert "She mentioned her scan is on Thursday." in context
    assert "7.0" not in context
