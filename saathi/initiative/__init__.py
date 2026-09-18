"""When Saathi speaks first. See SPEC.md, "Initiative": the third kind,
noticed, is the product; `policy.py` is what keeps it from becoming a
device that comments on everything.

Checkpoint 3's scope, deliberately: `scheduler.py` proposes candidates,
`policy.py` gates and logs each one, and *nothing calls `say()` from
here*. One pass looks like:

    from saathi.initiative import scheduler, policy

    candidates = scheduler.propose_candidates(store)
    context = policy.PolicyContext(presence=True)  # from a real signal, once one exists
    decisions = policy.evaluate(store, candidates, context)

`decisions` is exactly what got written to `initiatives` — read it (or
query `store.read("initiatives")` later) to see what this pass decided
and why, before anything is wired to speak it out loud.
"""
