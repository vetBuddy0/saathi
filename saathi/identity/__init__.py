"""Where Saathi's character lives, above the replaceable engine underneath
it (SPEC.md, "The rule everything follows"). `store.py` is the only file in
this package that touches SQLite directly, with one recorded exception:
`embed.backfill_embeddings` fills NULL `episodes.embedding` cells with a
single guarded UPDATE against `store.path` — write-once, never a
correction — rather than widening `IdentityStore` a second time next to
`retire` (DECISIONS.md, 2026-09-25).
"""
