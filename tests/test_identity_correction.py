"""identity/correction.py — the spoken correction tool. Goes through the
real tools/registry.py permission check with the new "memory" scope,
retires the named rule via IdentityStore.retire, records what she said
as a high-importance episode, and works from a thread that is not the
one that opened the store -- the exact shape of the bug that shipped in
c4d3717 and that every test then passed straight through.
"""

import json
import tempfile
import threading
from pathlib import Path

import pytest

from saathi.identity.compile import compile_context
from saathi.identity.correction import CORRECT_MEMORY_DESCRIPTION, make_correct_memory_tool
from saathi.identity.store import IdentityStore
from saathi.tools.llm_schema import tool_to_openai_schema
from saathi.tools.registry import PermissionDenied, Registry


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


@pytest.fixture
def persona(tmp_path):
    path = tmp_path / "persona.txt"
    path.write_text("You are warm.")
    return path


def _rule(store, text):
    return store.append(
        "rules", text=text, confidence=0.9, learned_at="2026-09-25T00:00:00+00:00",
        source_episode=None, active=1,
    )


def _registry(store):
    registry = Registry()
    registry.register(make_correct_memory_tool(store))
    return registry


def test_correction_by_rule_id_retires_the_rule_and_records_what_she_said(store, persona):
    rule_id = _rule(store, "Her daughter Priya visits on Saturdays.")
    result = _registry(store).call(
        "correct_memory",
        frozenset({"memory"}),
        said="that was my sister, not my daughter",
        rule_id=rule_id,
        correction="Priya is her sister",
    )

    assert result["status"] == "retired"
    assert result["retired"] == {"id": rule_id, "text": "Her daughter Priya visits on Saturdays."}
    assert store.read("rules", id=rule_id)[0]["active"] == 0

    episodes = store.read("episodes")
    assert len(episodes) == 1
    assert episodes[0]["importance"] >= 8
    assert "that was my sister, not my daughter" in episodes[0]["text"]
    assert "Priya is her sister" in episodes[0]["text"]
    assert "Her daughter Priya visits on Saturdays" in episodes[0]["text"]
    assert episodes[0]["id"] == result["episode_id"]

    context = compile_context(store, base_persona_path=persona)
    assert "[memory" not in context  # nothing active left to number
    assert "that was my sister, not my daughter" in context


def test_the_result_is_json_serialisable_and_steers_the_confirmation(store):
    rule_id = _rule(store, "She takes her tablets at eight.")
    result = _registry(store).call(
        "correct_memory", frozenset({"memory"}), said="no, that's wrong", rule_id=rule_id
    )
    json.dumps(result)  # cascade feeds this straight back to the model
    assert "next turn" in result["note"]
    assert "number" in result["note"]


def test_denied_without_the_memory_permission_and_the_handler_never_ran(store):
    rule_id = _rule(store, "She takes her tablets at eight.")
    registry = _registry(store)
    with pytest.raises(PermissionDenied):
        registry.call("correct_memory", frozenset(), said="forget that", rule_id=rule_id)
    with pytest.raises(PermissionDenied):
        registry.call(
            "correct_memory", frozenset({"preferences"}), said="forget that", rule_id=rule_id
        )
    assert store.read("rules", id=rule_id)[0]["active"] == 1
    assert store.read("episodes") == []


def test_the_handler_works_from_a_thread_that_did_not_open_the_store(store):
    # cli.handle_intent runs on the screen server's executor thread, not
    # the thread that opened `store`. A handler writing through the
    # shared connection passes every single-threaded test and raises
    # sqlite3.ProgrammingError live -- that exact bug shipped once.
    rule_id = _rule(store, "Her daughter Priya visits on Saturdays.")
    tool = make_correct_memory_tool(store)
    outcome: dict = {}

    def _run():
        try:
            outcome["result"] = tool.handler(said="that was my sister", rule_id=rule_id)
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            outcome["error"] = exc

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=5)
    assert "error" not in outcome, outcome.get("error")
    assert outcome["result"]["status"] == "retired"
    assert store.read("rules", id=rule_id)[0]["active"] == 0


def test_without_a_rule_id_the_best_overlapping_rule_is_retired(store):
    _rule(store, "She takes her tablets at eight.")
    target = _rule(store, "Her daughter Priya visits on Saturdays.")
    result = _registry(store).call(
        "correct_memory", frozenset({"memory"}), said="Priya doesn't visit on Saturdays any more"
    )
    assert result["status"] == "retired"
    assert result["retired"]["id"] == target
    assert store.read("rules", id=target)[0]["active"] == 0
    assert all(r["active"] == 1 for r in store.read("rules") if r["id"] != target)


def test_believed_is_matched_before_what_she_said(store):
    tablets = _rule(store, "She takes her tablets at eight.")
    _rule(store, "Her daughter Priya visits on Saturdays.")
    result = _registry(store).call(
        "correct_memory",
        frozenset({"memory"}),
        said="no, that's not right",
        believed="she takes her tablets at eight",
    )
    assert result["retired"]["id"] == tablets


def test_ties_go_to_the_newest_rule(store):
    _rule(store, "Priya visits on Saturdays.")
    newest = _rule(store, "Priya visits on Sundays.")
    result = _registry(store).call("correct_memory", frozenset({"memory"}), said="Priya visits")
    assert result["retired"]["id"] == newest


def test_no_overlap_retires_nothing_but_still_records_the_correction(store, persona):
    rule_id = _rule(store, "She takes her tablets at eight.")
    result = _registry(store).call("correct_memory", frozenset({"memory"}), said="forget that")
    assert result["status"] == "recorded"
    assert result["retired"] is None
    assert "ask her" in result["note"]
    assert store.read("rules", id=rule_id)[0]["active"] == 1
    episodes = store.read("episodes")
    assert len(episodes) == 1
    assert "forget that" in episodes[0]["text"]
    assert episodes[0]["importance"] >= 8
    # And the correction itself now reaches the context, so a wrong
    # *episode* (which nothing can retire) is answered by her own words.
    assert "forget that" in compile_context(store, base_persona_path=persona)


def test_a_stale_rule_id_never_falls_through_to_guessing_another_rule(store):
    # Rule 1 was retired last turn; the model's context still numbered
    # it. Rule 2 shares a word with what she said. Guessing rule 2 would
    # be a second wrong belief -- record, and ask.
    stale = _rule(store, "Priya visits on Saturdays.")
    store.retire("rules", stale, "earlier")
    other = _rule(store, "Priya is her daughter.")
    result = _registry(store).call(
        "correct_memory", frozenset({"memory"}), said="no, Priya doesn't visit", rule_id=stale
    )
    assert result["status"] == "recorded"
    assert result["retired"] is None
    assert "matched nothing" in result["note"]
    assert store.read("rules", id=other)[0]["active"] == 1
    assert len(store.read("episodes")) == 1


def test_a_rule_id_given_as_a_string_is_still_honoured(store):
    # Schema says integer; models emit "3" anyway, and Registry.call
    # passes arguments through untouched.
    rule_id = _rule(store, "She takes her tablets at eight.")
    result = _registry(store).call(
        "correct_memory", frozenset({"memory"}), said="that's wrong", rule_id=str(rule_id)
    )
    assert result["status"] == "retired"
    assert result["retired"]["id"] == rule_id


def test_the_correction_is_recorded_before_the_rule_is_retired(store, monkeypatch):
    # If the second write fails, the trail exists and the rule is still
    # active -- never a retired rule with no record of why.
    from saathi.identity import correction as correction_module

    rule_id = _rule(store, "She takes her tablets at eight.")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    original = correction_module.IdentityStore.retire
    monkeypatch.setattr(correction_module.IdentityStore, "retire", _boom)
    tool = make_correct_memory_tool(store)
    with pytest.raises(RuntimeError):
        tool.handler(said="that's wrong", rule_id=rule_id)
    monkeypatch.setattr(correction_module.IdentityStore, "retire", original)
    assert store.read("rules", id=rule_id)[0]["active"] == 1
    assert len(store.read("episodes")) == 1


def test_an_already_retired_rule_is_not_matched_again(store):
    rule_id = _rule(store, "Priya visits on Saturdays.")
    registry = _registry(store)
    registry.call("correct_memory", frozenset({"memory"}), said="Priya", rule_id=rule_id)
    result = registry.call("correct_memory", frozenset({"memory"}), said="Priya", rule_id=rule_id)
    assert result["status"] == "recorded"
    assert len(store.read("episodes")) == 2


def test_empty_said_changes_nothing(store):
    rule_id = _rule(store, "Priya visits on Saturdays.")
    result = _registry(store).call(
        "correct_memory", frozenset({"memory"}), said="  ", rule_id=rule_id
    )
    assert result["status"] == "error"
    assert store.read("rules", id=rule_id)[0]["active"] == 1
    assert store.read("episodes") == []


def test_the_correction_episode_is_written_without_an_embedding(store):
    # Inside a turn, no model may load; backfill_embeddings fills it later.
    rule_id = _rule(store, "Priya visits on Saturdays.")
    _registry(store).call("correct_memory", frozenset({"memory"}), said="wrong", rule_id=rule_id)
    assert store.read("episodes")[0]["embedding"] is None


def test_schema_and_description_shape(store):
    tool = make_correct_memory_tool(store)
    assert tool.name == "correct_memory"
    assert tool.permission == "memory"
    assert tool.schema["required"] == ["said"]
    assert set(tool.schema["properties"]) == {"said", "rule_id", "believed", "correction"}
    schema = tool_to_openai_schema(tool, CORRECT_MEMORY_DESCRIPTION)
    assert schema["function"]["name"] == "correct_memory"
    assert "wrong" in schema["function"]["description"]
