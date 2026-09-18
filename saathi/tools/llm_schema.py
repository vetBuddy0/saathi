"""`Tool` -> the JSON shape Groq's (OpenAI-compatible) function-calling
API expects. Deliberately separate from `registry.py`: `Tool` has no
`description` field, and adding one would be a structural change to a
protected interface (CLAUDE.md's five) for the sole benefit of one
caller (an LLM's tool-selection prompt) that every other use of `Tool`
(the permission check, the stub tests) has no need for. Descriptions are
supplied here instead, alongside the tools that need them.
"""

from __future__ import annotations

from saathi.tools.registry import Tool


def tool_to_openai_schema(tool: Tool, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": description,
            "parameters": tool.schema,
        },
    }
