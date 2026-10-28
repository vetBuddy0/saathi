"""Medication support for a care home: the eMAR connector, the local
record of what nurses and residents said about doses, and the safety
rules around both.

Why it is its own package with its own SQLite file, not tables in the
identity store: the identity file is one person's memory (SPEC.md,
"Memory"); this is a ward's schedule, read from the home's eMAR, and its
audit log is the home's record, not hers. Keeping it apart also leaves
`IdentityStore` -- one of the five interfaces -- untouched. Lost: adding
`residents`/`doses` to `identity/store.py`'s schema, which would have
meant `append`/`read` on clinical data with `retire` the only correction,
and her identity file growing other people's medication.

Nurses give every dose; residents never self-medicate (DECISIONS
2026-10-08). Nothing in here reminds a resident to take anything.
"""
