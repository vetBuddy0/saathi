# Memory stream — `IdentityStore.retire`, the correction tool, local embeddings

Branch `batch/memory`, 2026-09-25. Territory: `saathi/identity/**`,
`tests/test_identity_*.py`, `tests/conftest.py`, `pyproject.toml` (one
dependency line), `DECISIONS.md` (appended), this file. Nothing outside
that was edited; what needs to change elsewhere is under
"Cross-territory edits needed" at the bottom, as exact diffs.

## What was built

**1. `IdentityStore.retire(table, row_id, at)`** — `saathi/identity/store.py`.
The fourth verb, approved by the user. `rules`/`reminders`: `active`
becomes 0. `edges`: `until` becomes `at`. Nothing is deleted; what was
once believed stays readable. It is not `update()`: no column name, no
value. `UnknownTable` for a table outside the schema, `NotRetirable`
for a table with nothing to retire (`episodes`, `preferences`, `turns`,
`initiatives`, `entities`), `UnknownRow` when the id matches nothing —
never a silent no-op. Idempotent on an already-retired row. The module
docstring now records why the fourth verb won and the option that lost
(append-only retraction-event tables, checkpoint-3.md option b).

**2. `profile.retract_rule(store, rule_id, *, at=None)`** is real: it
calls `retire("rules", ...)`. `RetractionNotSupported` is gone (dead
code that lied). `list_rules(include_inactive=True)` still shows the
retired rule, so the family can see what was believed.

**3. `correct_memory`** — `saathi/identity/correction.py`,
`make_correct_memory_tool(store) -> Tool`, permission `"memory"` (a
fourth scope), `CORRECT_MEMORY_DESCRIPTION` kept out of `Tool` as
`tools/llm_schema.py` requires. Arguments: `said` (required, her
words), `rule_id` (the `[memory N]` number from the context),
`believed` (the model's quote of the wrong thing, fallback matcher),
`correction` (what is true, if she said). It retires the rule and
writes an `episodes` row at importance 9 whose text carries what she
said, what is true, and the belief that was retired — no new table.
The handler opens its own connection from `store.path`; a test calls it
from a second thread. The result is JSON-serialisable and its `note`
steers the confirmation ("one sentence, no memory number aloud").

**4. Rules are addressable.** `compile_context` sends each active rule
as `[memory N] sentence` (N = `rules.id`) preceded by one sentence
saying the numbers exist only for `correct_memory` and are never to be
said aloud. Without a number the tool matches content words of
`believed`, then `said`, against active rules; ties go to the newest;
no overlap retires nothing, records the correction anyway (a wrong
belief can live in an episode, which nothing can retire — her own
words at importance 9 next to it are the fix), and asks the model to
ask her.

**5. Local embeddings** — `saathi/identity/embed.py`.
`sentence-transformers/all-MiniLM-L6-v2` (Apache 2.0) through
`onnxruntime` (MIT, already installed via Piper, now an explicit
dependency). Pure-Python BERT WordPiece over `vocab.txt`; mean-pool
over the attention mask; L2-normalise; stored as little-endian float32
bytes, exactly what `compile.retrieve_episodes` already reads. Model
files live in `~/.saathi/embeddings/`, fetched only by an explicit
`python -m saathi.identity.embed --download` / `--selfcheck` /
`--backfill`; `embed()` never touches the network and returns `None`
when the files are absent, so everything degrades exactly as before.
Session creation is lazy (first `embed()`), never at import or in
`__init__`; `onnxruntime` is not even imported until then.

Plug points: `digest.write_episode(..., embedding=None, embedder=embed)`
computes the vector at write time — called only from the cascade's
post-`say()` background thread, so this is "between turns" with no
cascade change. `compile_context()` uses the newest episode's stored
vector as the query when no caller passes one. `backfill_embeddings(
store, embedder=None, limit=None)` fills NULL rows newest-first.

**6. `tests/conftest.py`** — one autouse fixture pointing the default
embedder at an empty directory, so no test loads the real model
whatever is in `~/.saathi` on the machine running the suite.

## Code review findings, fixed before commit

`/code-review high` on the diff surfaced ten items; all are fixed and
tested, and each has a DECISIONS entry:

1. A stale explicit `rule_id` fell through to the fuzzy matcher and
   could retire a *different* rule — now records and asks, never
   guesses.
2. `rule_id` passed as `"3"` was treated as stale — coerced with `int()`.
3. Retire then write: a failed second write left a retired rule with
   no trail — the episode is written first now.
4. A correction was undone by the next `reflect()` pass (its source
   episode is still there) — retired rules go into the insights prompt
   as "known wrong", as sentences.
5. `retire("edges")` overwrote `until` on a second call — `COALESCE`.
6. A truncated or different-dimension embedding blob raised inside
   `compile_context`, on the constructor thread — scores 0 like NULL.
7. An exception from `InferenceSession.run` propagated out of
   `write_episode` on the post-`say()` thread — returns `None`, logged.
8. Download URLs were `resolve/main` with a byte-count check — pinned
   to commit `1110a24…` and verified by sha256; re-run live afterwards.
9. `urlopen` had no timeout — 60 s.
10. Backfill built one padded batch for the whole table — chunks of 64.

## Verified

- Suite: 356 passed, 2 skipped (x86, this machine); `ruff check` and
  `ruff check --select=F821 saathi` clean. arm64 is CI's to confirm — the
  only new wheel dependency is `onnxruntime`, whose `1.30.0` lock entry
  already has `manylinux_2_28_aarch64` for cp312.
- `python -m saathi.identity.embed --selfcheck`: model download 46.8 s;
  first embed (session load + one sentence) 288 ms; three sentences
  warm 14 ms. Cosines: scan~appointment 0.389, scan~tea 0.197,
  appointment~tea 0.066. Session load alone measured at 186 ms.
- `--backfill --db <scratch>` on five seeded NULL rows: 5 filled in
  0.28 s, all 384-dim, unit norm. `write_episode` through the default
  embedder with the model present: 8 ms.
- Boot: `SAATHI_SCREEN_PORT=8766 SAATHI_IDENTITY_DB=<scratch> saathi
  run` answered HTTP 200 within 2 s; `onnxruntime` absent from
  `sys.modules` after importing `saathi.cli` and every identity module
  and constructing the default embedder (148 ms for all of it).
- Licence and URLs checked live with `curl -sI` on 2026-09-25:
  `license:apache-2.0`; `onnx/model.onnx` 90,405,214 bytes;
  `vocab.txt` 231,508 bytes. Downloads are pinned to repository commit
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` and verified by sha256
  (`model.onnx` `6fd5d72f…6452`, equal to HuggingFace's LFS etag;
  `vocab.txt` `07eced37…38a3`). The pinned path was exercised end to
  end with `--selfcheck --model-dir <scratch>` after the review fixes.

## Found, and reported rather than fixed

**Relevance rarely wins alone under equal-weight min-max.** Against a
six-episode scratch store, "how does she take her tea" scored the tea
episode cosine 0.69 vs ≤ 0.34 for everything else — normalised
relevance 1.00 vs ≤ 0.39 — which moved it from last (not sent at
top_k = 5) to fourth (sent). It still trailed the newest episode
(recency 1.00 + importance 0.50). Min-max stretches a five-hour recency
spread to the full [0, 1]. This is `compile.py`'s documented
equal-weight design; retuning it changes what she hears and is the
user's call. If the answer is "relevance should be able to win", the
smallest change is a floor on recency's range (don't stretch
differences smaller than, say, a day) — not a weight.

**Nothing writes `entities` or `edges`.** Stated plainly, as asked:
`entities` appears only in the schema, FK references, a docstring and
the store round-trip test; `edges` only in the schema and the
reachability test; `since`/`until` only at the schema; `entity_id` is
only ever written as `None`. `edges.since`/`edges.until` are declared,
never written. `retire("edges", ...)` is implemented and tested against
rows inserted by the test itself, and it is complete as a primitive —
but because `read()` is `SELECT *` and `edges` has no `id`, no caller
can obtain an edge's `rowid` through the interface today. Making
`since/until` real means building entity resolution first (TODO.md
already says so); a writer was not invented.

**For the Calling stream, which will be the first writer of
`entities`/`edges`** (phone numbers as JSON in `entities.notes`): the
`rowid` gap above lands on you directly. `append("edges", ...)` returns
the `rowid`, so a writer that keeps the return value can `retire` its
own edges; anything that later *finds* an edge via `read("edges", src=…,
dst=…)` cannot, because `SELECT *` never includes `rowid`. The two
honest fixes are a schema change (`edges.id INTEGER PRIMARY KEY`, a
SPEC.md diff — then `retire("edges")` keys on `id` like the other
tables, one line in `_RETIREMENT`) or `read()` surfacing `rowid` for
tables without an `id`. Neither was taken here because nothing wrote
edges yet; whichever stream writes them first should raise it. Also
note `retire("edges")` uses `COALESCE(until, ?)`: the first end date
stands, a second retire does not move it.

**`set_language` may already be exposed to the cross-thread bug.**
`saathi/tools/language.py`'s handler calls `write_preference(store,
...)` on the shared connection, and `cli.handle_intent` runs it on the
screen server's executor thread — the same thread the correction
handler had to be protected from. It passes every test because no test
calls it from another thread. Not mine to fix (`tools/`); the fix is
the same `IdentityStore(store.path)` pattern `correction.py` uses. A
one-line test in `tests/test_tools_language.py` calling the handler
from a `threading.Thread` would confirm or clear it.

**One tool call per turn** (`cascade.py`, DECISIONS 2026-09-18). With
two tools offered, a turn that wants both `set_language` and
`correct_memory` gets only the first. The correction tool is designed
around this (numbers in context, no `list` action), but it is a
standing limitation in voice/ territory.

## Tests whose expectations changed

Said explicitly, per CLAUDE.md ("if a test looks wrong, say so"):

- `tests/test_identity_profile.py::test_retract_rule_raises_retraction_not_supported`
  asserted the raise **and** that `active` stayed 1. With `retire()`
  the behaviour genuinely changes on the user's instruction; replaced
  by `test_retract_rule_makes_the_rule_inactive_but_keeps_it_reviewable`
  and `test_retract_rule_with_a_stale_id_raises_not_silently_succeeds`.
- `tests/test_identity_digest.py::test_write_episode_appends_a_row_with_entity_and_embedding_left_null`
  asserted `embedding is None` as a deliberate gap. The gap is closed;
  renamed to `..._with_entity_left_null`, and it now asserts NULL only
  as the no-model degraded state that `conftest.py` guarantees. The
  `entity_id is None` assertion is unchanged and still true.

No other existing test changed.

## Decisions

All in `DECISIONS.md` under 2026-09-25: `retire` (the user's), `at`
unused for `rules`/`reminders`, edges by `rowid`, correction as an
episode at importance 9 (the user's), always record even with no
match, `[memory N]` numbering, the fuzzy fallback, the `"memory"`
scope, own connection in the handler, NULL embedding on the correction
episode, MiniLM via ONNX with a hand-written tokenizer and no new
tokenizer/hub dependency, English-only model, no implicit download,
newest-episode query, backfill's direct SQL, `conftest.py`, the two
test changes, one intra-op thread, and the relevance-vs-recency
finding.

## Debt left

- `backfill_embeddings` is not invoked anywhere between turns yet; the
  correction episode's vector stays NULL until it is. The one-line
  cascade change is below. Until then `--backfill` is a manual step.
- The download is not in `scripts/setup-pi.sh` (below). Until it is,
  a fresh device has relevance = 0, exactly as today.
- `retire` on `reminders` has no caller. `builtin.py`'s
  `set_reminder`/`cancel_reminder` (SPEC's module map, not started)
  are where it belongs.
- `initiatives.spoken` still has no way to flip (checkpoint-3.md named
  it). `retire` deliberately does not cover it — "spoken" is not
  "stopped being true". It needs its own decision.
- The `[memory N]` prefix has not been heard live through Groq; the
  guard sentence is untested against a real model saying a number
  aloud. Needs a real run once `cli.py` registers the tool.
- `_STOPWORDS` in `correction.py` is English-only, like the model.
- The "known wrong" section in `reflect.py`'s insights prompt is a
  prompt instruction, not a guarantee; a model can still restate a
  retired belief. The deterministic post-filter was rejected (it drops
  the corrected version too). A live reflection run over a store with
  a retired rule is the check that hasn't happened.
- The correction handler's two writes are two commits, not one
  transaction; the ordering (episode, then retire) bounds the failure
  but does not remove it. A single transaction needs either a
  `transaction()` context on the interface or the handler reaching into
  the connection — both were declined.

## Proposed SPEC.md diff — not applied

```diff
 ## Memory
@@
-`preferences.key` is deliberately not unique. `append()`-only writes
-(`IdentityStore`'s whole interface) can't express "update the row for
-this key" — a `PRIMARY KEY` on `key` made the *first* write to a key
+`preferences.key` is deliberately not unique. `append()`-only writes
+can't express "update the row for this key" — a `PRIMARY KEY` on `key`
+made the *first* write to a key
@@
-Vector search via `sqlite-vec`. At a few thousand episodes, brute-force cosine
-in numpy is also fine.
+`IdentityStore` has four verbs: `create`, `append`, `read`, `retire`.
+`retire(table, row_id, at)` is the one narrow "this stopped being true"
+primitive — `active = 0` on `rules`/`reminders`, `until = at` on
+`edges` — not a general `update()`. An append-only store that cannot
+be corrected is worse than one that forgets.
+
+Embeddings are computed on the device, between turns, by a local
+sentence model (`all-MiniLM-L6-v2` via `onnxruntime`, Apache 2.0, ~90 MB
+in `~/.saathi/embeddings/`). Not a vendor and not a vector DB. With the
+model absent, relevance is 0 and retrieval runs on recency + importance.
+Vector search is brute-force cosine in numpy; `sqlite-vec` remains an
+option at a scale this device will not reach for years.
@@
 **Every rule records where it came from**, and `profile.py` exposes the set
-readable and editable. A companion that learns something wrong about someone's
-mother with no way to correct it is a support call nobody can answer.
+readable and editable (`retract_rule`), and she can correct it herself
+by voice: `correct_memory` (permission `memory`) retires the belief she
+contradicts and records what she said as a high-importance episode.
+Each active rule reaches the model numbered, so it can say which one;
+the number is never spoken. A companion that learns something wrong
+about someone's mother with no way to correct it is a support call
+nobody can answer.
```

And under "Tools": the permission scopes are `calls`, `music`,
`preferences`, `memory`.

## Cross-territory edits needed

### `saathi/cli.py` — register the tool, grant the scope, offer the schema

```diff
@@ def _run() -> int:
+        from saathi.identity.correction import (
+            CORRECT_MEMORY_DESCRIPTION,
+            make_correct_memory_tool,
+        )
         from saathi.identity.preferences import (
             LANGUAGE_KEY,
             TTS_BACKEND_KEY,
             threadsafe_reader,
         )
@@
         registry = Registry()
         set_language_tool = make_set_language_tool(store)
         registry.register(set_language_tool)
-        granted_permissions = frozenset({"preferences"})
+        # correct_memory: her own way to fix a wrong belief. Like
+        # set_language, no external-world consequence -- granted
+        # unconditionally (DECISIONS.md 2026-09-25, "memory" scope).
+        correct_memory_tool = make_correct_memory_tool(store)
+        registry.register(correct_memory_tool)
+        granted_permissions = frozenset({"preferences", "memory"})
@@
-        tool_schemas = [tool_to_openai_schema(set_language_tool, SET_LANGUAGE_DESCRIPTION)]
+        tool_schemas = [
+            tool_to_openai_schema(set_language_tool, SET_LANGUAGE_DESCRIPTION),
+            tool_to_openai_schema(correct_memory_tool, CORRECT_MEMORY_DESCRIPTION),
+        ]
```

### `saathi/voice/engine/cascade.py` — backfill between turns; optionally a live query

In `_after_turn_in_background._run`, the final block (currently around
line 708–713):

```diff
             if store_path is not None:
                 # Same fresh-connection rule as
                 # _refresh_compiled_context_in_background(): sqlite3
                 # connections can't cross threads.
                 with IdentityStore(store_path) as background_store:
-                    self._compiled_context = compile_context(background_store)
+                    # Between turns: vectors for rows written without
+                    # one (older rows, correction episodes). Bounded so
+                    # a long-lived device never does the whole table
+                    # here; --backfill exists for that.
+                    backfill_embeddings(background_store, limit=20)
+                    self._compiled_context = compile_context(background_store)
```

with `from saathi.identity.embed import backfill_embeddings` alongside
the existing `saathi.identity` imports. That is the load-bearing part.

Optionally, a live query instead of the newest-episode default:

```diff
-                    self._compiled_context = compile_context(background_store)
+                    query = embed(exchange.user) if exchange is not None else None
+                    self._compiled_context = compile_context(
+                        background_store, query_embedding=query
+                    )
```

Honest caveat before taking that second hunk: the newest episode *is*
the digest's English observation of this same turn, so the two are
nearly equivalent; the live utterance only helps on turns where the
digest returned no observation, and it is in whatever language she
spoke, which the English-only model handles worse. I would take the
backfill hunk and leave the query as is.

### `scripts/setup-pi.sh` — fetch the model once at install

Next to `install_piper_voices`:

```bash
install_embedding_model() {
    log "embedding model (identity/embed.py, ~90MB, Apache 2.0)"
    run_as_saathi "cd '$REPO_ROOT' && '$UV_BIN' run python -m saathi.identity.embed --download" \
        || die "failed to download the embedding model. Check network access and the output above."
}
```

and call it where the voices are installed. Idempotent: `--download`
is a no-op when the files exist.

### `tests/test_tools_language.py` — confirm or clear the cross-thread exposure

```python
def test_set_language_handler_works_from_a_thread_that_did_not_open_the_store(store):
    tool = make_set_language_tool(store)
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(result=tool.handler(language="chinese"))
    )
    thread.start(); thread.join(5)
    assert outcome.get("result", {}).get("status") == "ok"
```

If it fails with `sqlite3.ProgrammingError`, the fix is `correction.py`'s
pattern: `with IdentityStore(store.path) as own: write_preference(own, ...)`.
