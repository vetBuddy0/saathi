"""Tools: what the core is allowed to execute on the voice engine's behalf.

The voice engine emits intent; it never executes anything itself
(SPEC.md, "The five interfaces"). Every call into a tool goes through
`registry.py`'s permission check first — including stubs, which is why
`stubs.py` exists in v1 at all rather than waiting for v2's real
implementations.
"""
