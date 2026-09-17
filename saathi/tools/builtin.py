"""`remember`, `recall`, `time`, `set_reminder` — not built at checkpoint 1.

Exists once `identity/store.py` has more than create/append/read to call:
`remember`/`recall` need `compile.py`'s retrieval (checkpoint 3),
`set_reminder` needs the `reminders` table wired to something that acts on
it. Will register through `tools/registry.py` exactly like `stubs.py` does.
"""
