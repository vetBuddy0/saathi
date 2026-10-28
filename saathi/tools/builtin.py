"""`remember`, `recall`, `time` — not built at checkpoint 1. (`set_reminder`
was built on 2026-10-20, in initiative/reminders.py beside what speaks it.)

Exists once `identity/store.py` has more than create/append/read to call:
`remember`/`recall` need `compile.py`'s retrieval (checkpoint 3),
`set_reminder` needs the `reminders` table wired to something that acts on
it. Will register through `tools/registry.py` exactly like `stubs.py` does.
"""
