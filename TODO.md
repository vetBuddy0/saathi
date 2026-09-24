# Known problems and open decisions

What is known to be wrong or unfinished, with the date it was noticed
and what is actually known about it. Not a wishlist. When an item is
fixed, move it to `docs/completed/`; when a decision is made, record it
in `DECISIONS.md` and strike it here.

## Known problems

**2026-09-25 — "The feel is still missing." (user's words)**
Reported after the three memory layers landed and were tested live:
she works — stays quiet on silence, follows a conversation — but does
not yet feel like a person. Not diagnosed. Nothing below is confirmed
as the cause; these are the things known to be true that could
plausibly contribute, listed so the investigation starts from evidence
rather than guesses:
- `persona_stub.txt` is still a stub. It is the user's file to write
  (`compile.py` never invents identity content), and everything she
  sends starts from it.
- Replies are capped at two sentences (`compile.py`'s
  `_LENGTH_CAP_SENTENCE`) — deliberate, for cost and fatigue, but it
  may read as clipped.
- p95 time-to-first-audio is 1213 ms against a 1200 ms budget (see
  `docs/completed/latency-investigation.md`); a beat too slow reads
  as hesitant. The new memory layers add prompt tokens per turn and
  have not yet been measured against that number.
- Long-term retrieval ranks by recency + importance only; relevance
  is a constant (see below). She may recall the wrong thing.
- One observed reply spliced a Chinese token into Hindi
  (`存在的`, 2026-09-22 log) — a `qwen/qwen3.8-27b` artifact.
- The ambient screen layer (time/date/name/reminder, eyes scaling
  with attention) deferred from checkpoint 1 is still not built.
Next step: collect five or six real exchanges where it felt wrong,
with what she said, before changing anything.

**Retrieval's relevance axis is dead.** Every `episodes.embedding` is
NULL: Groq offers no embedding model, and CLAUDE.md rules out a vector
DB or a second vendor. `retrieve_episodes` degrades to recency +
importance, as its docstring says — but SPEC.md's "recency + importance
+ relevance" is currently two out of three. Needs an embedding source
decided (a vendor question — the user's).

**Latency budget is red.** 1213 ms p95 vs 1200 ms. Left deliberately
(DECISIONS.md, 2026-09-19) until Google streaming TTS is unblocked;
further Piper tuning would be thrown away.

**The digest costs one extra model call per turn.** Background, never
in the hot path, but it is real spend. Not yet measured in
`turns.cost_usd` (which counts the reply call only). Worth adding.

**Whisper still hallucinates on quiet-but-voiced audio.** The VAD gate
stops silence from reaching STT; a cough or a chair scrape that Silero
counts as ~96 ms of voice still goes through and may come back as
"Thank you." Rarer, not gone.

## Open decisions (waiting on the user)

- **`IdentityStore.retire(table, row_id, at)`** — one narrow "this is
  no longer true" primitive (flips `rules.active`/`reminders.active`,
  sets `edges.until`). One of the five interfaces, so not decided
  alone. Blocks the correction tool ("no, that was my sister") and
  making `edges.since/until` real. See `identity/profile.py`'s
  docstring for the same block, hit earlier.
- **Nothing writes `entities` or `edges` at all.** Making `since/until`
  real means building entity resolution from turns first, not just
  filling two columns.
- **SPEC.md Memory-section diff** (three layers, silent turns, real
  prompt-token count) — proposed 2026-09-24, not applied. The user
  applies spec edits.
- **Where a correction is recorded** — an `episodes` row with high
  importance, or a `corrections(id, ts, rule_id, said)` table (a
  schema diff).

## Dev-machine notes

- `pytest` on this box needs `env PYTHONPATH= …` — the shell profile
  leaks ROS Foxy's py3.8 plugins into the venv and pytest dies at
  startup. Not a repo problem; CI never sees it.
- The raw mic on this box has a clipping noise floor (ERLE ~11 dB vs
  the 25–30 dB target). The echo-cancelled source measured clean
  (RMS 1–750) on 2026-09-24, so this may have been the raw path only.
