"""`call_contact`, `play_music` — stubs, going through the same permission
check as any real tool would.

SPEC.md, "Scope": calls and music are *out* for v1, but "their tool stubs
exist in v1 so v2 swaps an implementation rather than inventing plumbing."
That only holds if the stub is wired exactly like a real tool would be —
same `Tool` shape, same registry, same permission gate — so v2 replaces
`handler` and nothing else. A handler that skipped the permission check
because it doesn't do anything real yet would be plumbing v2 has to invent
anyway.
"""

from __future__ import annotations

from typing import Any

from saathi.tools.registry import Registry, Tool


def _call_contact(contact: str) -> dict[str, Any]:
    return {"status": "stub", "tool": "call_contact", "detail": "calling arrives in v2"}


def _play_music(query: str) -> dict[str, Any]:
    return {"status": "stub", "tool": "play_music", "detail": "music arrives in v2"}


CALL_CONTACT = Tool(
    name="call_contact",
    schema={
        "type": "object",
        "properties": {"contact": {"type": "string"}},
        "required": ["contact"],
    },
    permission="calls",
    handler=_call_contact,
)

PLAY_MUSIC = Tool(
    name="play_music",
    schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    permission="music",
    handler=_play_music,
)


def register(registry: Registry) -> None:
    registry.register(CALL_CONTACT)
    registry.register(PLAY_MUSIC)
