import pytest

from saathi.tools import stubs
from saathi.tools.registry import PermissionDenied, Registry, Tool, UnknownTool


def test_call_invokes_handler_when_permission_granted():
    registry = Registry()
    registry.register(
        Tool(name="time", schema={}, permission="clock", handler=lambda: "it's time")
    )
    assert registry.call("time", frozenset({"clock"})) == "it's time"


def test_call_denies_without_permission():
    registry = Registry()
    registry.register(Tool(name="time", schema={}, permission="clock", handler=lambda: "now"))
    with pytest.raises(PermissionDenied):
        registry.call("time", frozenset())


def test_unknown_tool_raises():
    registry = Registry()
    with pytest.raises(UnknownTool):
        registry.call("nonexistent", frozenset())


def test_permission_is_checked_before_the_handler_runs():
    registry = Registry()
    calls = []
    registry.register(
        Tool(
            name="dangerous",
            schema={},
            permission="scary",
            handler=lambda: calls.append("ran"),
        )
    )
    with pytest.raises(PermissionDenied):
        registry.call("dangerous", frozenset())
    assert calls == []


def test_stubs_go_through_the_same_permission_check():
    registry = Registry()
    stubs.register(registry)

    with pytest.raises(PermissionDenied):
        registry.call("call_contact", frozenset(), contact="daughter")
    with pytest.raises(PermissionDenied):
        registry.call("play_music", frozenset(), query="bhajans")

    result = registry.call("call_contact", frozenset({"calls"}), contact="daughter")
    assert result["status"] == "stub"

    result = registry.call("play_music", frozenset({"music"}), query="bhajans")
    assert result["status"] == "stub"
