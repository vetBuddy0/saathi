"""saathi/tools/language.py — the first real (non-stub) tool, and item
G's spoken entry point: "speak to me in Mandarin" writes through the
exact same identity/preferences.py path the Ctrl+L panel uses.
"""

import tempfile
from pathlib import Path

import pytest

from saathi.identity.preferences import LANGUAGE_KEY, read_preference, write_preference
from saathi.identity.store import IdentityStore
from saathi.tools.language import make_set_language_tool
from saathi.tools.llm_schema import tool_to_openai_schema
from saathi.tools.registry import PermissionDenied, Registry


@pytest.fixture
def store():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()
        yield store


def test_set_language_writes_through_the_same_path_as_the_panel(store):
    tool = make_set_language_tool(store)
    result = tool.handler(language="chinese")
    assert result["status"] == "ok"
    assert result["language"] == "chinese"
    assert read_preference(store, LANGUAGE_KEY) == "chinese"


def test_set_language_result_explains_the_switch_is_effective_next_turn(store):
    # Found by a real end-to-end run, not guessed: without this, the
    # model doesn't know the switch already succeeded and phrases a
    # confusing reply that sounds like a refusal. See language.py's
    # docstring on the "ok" branch for the actual incident.
    tool = make_set_language_tool(store)
    result = tool.handler(language="chinese")
    assert "next turn" in result["note"]


def test_set_language_rejects_an_unsupported_language_without_writing_anything(store):
    tool = make_set_language_tool(store)
    result = tool.handler(language="klingon")
    assert result["status"] == "unsupported"
    assert read_preference(store, LANGUAGE_KEY) is None


def test_set_language_reports_preference_locked_instead_of_raising(store):
    # The real, current limitation from identity/preferences.py: a
    # second write to the same key fails under today's schema. The tool
    # must surface that as an ordinary result the model can apologize
    # about, not an exception that takes the turn down.
    write_preference(store, LANGUAGE_KEY, "english")
    tool = make_set_language_tool(store)
    result = tool.handler(language="chinese")
    assert result["status"] == "locked"
    assert read_preference(store, LANGUAGE_KEY) == "english"  # unchanged


def test_set_language_goes_through_the_real_permission_check(store):
    registry = Registry()
    registry.register(make_set_language_tool(store))

    with pytest.raises(PermissionDenied):
        registry.call("set_language", frozenset(), language="chinese")

    result = registry.call("set_language", frozenset({"preferences"}), language="chinese")
    assert result["status"] == "ok"


def test_schema_enum_matches_supported_languages_exactly(store):
    from saathi.voice.language import SUPPORTED_LANGUAGES

    tool = make_set_language_tool(store)
    assert set(tool.schema["properties"]["language"]["enum"]) == set(SUPPORTED_LANGUAGES)


def test_tool_to_openai_schema_shape(store):
    tool = make_set_language_tool(store)
    schema = tool_to_openai_schema(tool, "a description")
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "set_language"
    assert schema["function"]["description"] == "a description"
    assert schema["function"]["parameters"] is tool.schema
