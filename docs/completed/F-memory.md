# F — Memory

## What was built

`identity/compile.py`'s `compile_context()` is now the actual source of
what the engine receives — `cascade.py` no longer reads `persona_stub.txt`
directly. It reads that same file as its base layer and folds in active
`rules` and retrieved `episodes`, all as plain sentences (never
confidence scores or timestamps — "stored and sent are different,"
SPEC.md). `retrieve_episodes()` implements *Generative Agents*' (Park et
al. 2023) recency + importance + relevance scoring, each independently
min-max normalized before summing. Relevance degrades to a flat,
ranking-neutral 0 when there's no query embedding or an episode has
none stored — true everywhere in this codebase today, since nothing
writes an embedding yet (`reflect.py`, which now does write real rules,
is checkpoint 3 scope and doesn't touch embeddings either).

`cascade.py`'s `_refresh_compiled_context_in_background()` is where
SPEC.md's "compiled between turns, never during one" is actually
enforced: once at construction (there's no previous turn to trigger a
refresh from, and context must already be held "when she starts
speaking"), and again after every `say()` call, in a background thread.

The two-sentences-unless-more-is-needed reply cap moved out of
`persona_stub.txt` (identity content, the user's file) into
`compile.py` itself (an engineering constraint, not identity).

## Verified

A real Groq call, not just mocked tests: a stored rule ("she likes being
greeted by name, Margaret") actually changed what the model said —
"Good morning, Margaret" — pulled from `IdentityStore`, not the static
file.

## Found and fixed along the way

A real bug, not from this session's own design but exposed by it:
`sqlite3` connections can't cross threads. The background context
refresh crashed with `ProgrammingError` reusing the caller's connection
from a different thread. Fixed by giving the background thread its own
connection to the same file (SQLite's own supported way to do this) —
added a small, read-only `IdentityStore.path` property to make that
possible, rather than flipping `check_same_thread` off on the shared
connection (which would have changed `IdentityStore`'s threading
contract for every caller to suit one background job).

## Not done / left open

- No embedding model is wired in anywhere — relevance scoring is real
  code, fully tested, but currently always contributes 0 (recency +
  importance only) since nothing produces an embedding to score
  against. Picking and wiring one (which model, local vs. API, cost) is
  a real, undecided question, not attempted here.
- `identity/reflect.py` (the thing that would actually populate
  `episodes`/`rules` with real content from real conversations) is
  checkpoint 3 scope — started this session (see
  `docs/completed/checkpoint-3.md`), not part of item F itself.
