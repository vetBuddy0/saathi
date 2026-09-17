"""Episodes -> rules, with provenance. Not built until checkpoint 3.

This is where "noticed" initiative comes from: reflection writes *she has a
scan Thursday and is anxious*, and `initiative/scheduler.py` queries for
things worth following up on. Every rule it writes must record
`source_episode` — SPEC.md: a companion that learns something wrong about
someone's mother with no way to correct it is a support call nobody can
answer, and `identity/profile.py` is that way to correct it.
"""
