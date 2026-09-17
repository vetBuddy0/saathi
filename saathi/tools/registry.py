"""`Tool` — one of the five interfaces: name, schema, permission, callable.

"The voice engine never executes anything. It emits intent; the core
validates; the tool executes" (SPEC.md). This module is the validator: every
call to `Registry.call()` checks the tool's required permission against
what the caller was granted *before* the handler runs — including stub
tools (`stubs.py`), which is exactly why checkpoint 1 builds this now
instead of waiting for the first real tool. Skipping the check for a stub
because "it doesn't do anything real yet" would be reaching through the
interface (CLAUDE.md) for a reason that stops being true the day v2 fills
the stub in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class Tool:
    name: str
    schema: Mapping[str, Any]  # JSON-schema-shaped description of arguments
    permission: str  # scope required to call this tool
    handler: Callable[..., Any]


class UnknownTool(KeyError):
    pass


class PermissionDenied(PermissionError):
    def __init__(self, tool_name: str, permission: str) -> None:
        super().__init__(f"{tool_name!r} requires permission {permission!r}")
        self.tool_name = tool_name
        self.permission = permission


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(name) from None

    def __iter__(self):
        return iter(self._tools.values())

    def call(self, name: str, granted: frozenset[str], **args: Any) -> Any:
        """Invoke `name` with `args`, only if `granted` contains the tool's
        required permission. Raises `PermissionDenied` otherwise, before
        the handler ever runs."""
        tool = self.get(name)
        if tool.permission not in granted:
            raise PermissionDenied(tool.name, tool.permission)
        return tool.handler(**args)
