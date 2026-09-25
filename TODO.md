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
- **The voice itself.** Every reply she hears is Piper, which is flat
  and synthetic, and no amount of memory or persona text survives a
  flat delivery. Absent from this list until 2026-09-25 and the most
  likely cause, above the length cap and above 13 ms of latency. Google
  Chirp3-HD is now real (`saathi/voice/tts/google_backend.py`, the same
  speaker in English and Mandarin) and selectable in the Ctrl+L panel;
  the six comparison files are in `~/.saathi/tts-compare/` and
  `docs/completed/voice.md` has the numbers. Listening is the next
  step, and it is the user's. Known cost: as wired today (one WAV per
  sentence) Chirp adds ~500 ms to time-to-first-audio over Piper; the
  raw-PCM playback path proposed in that doc brings it to ~330 ms.
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

~~**YouTube titles are read and shown raw.**~~ Fixed 2026-09-26
(`clean_title` in `saathi/tools/media.py`, per-sentence voice by script
in `cascade.py`; see DECISIONS.md and `docs/completed/screen.md`,
"Fixed later"). The model garbling a character when repeating a title
(林淑容 → 林深容) remains a model artifact: the offer is still phrased by
the model so it can be framed in her language.

**Retrieval's relevance axis is live but weak.** ~~Every
`episodes.embedding` is NULL~~ — resolved 2026-09-25 by PR #2: local
MiniLM via onnxruntime, embedded between turns. Remaining: under
equal-weight min-max, relevance lifts an episode into the sent set but
rarely beats recency + importance (numbers in DECISIONS.md). Not
retuned — retuning changes what she hears, so it's the user's call.
`backfill_embeddings` is not yet invoked between turns (cascade.py diff
in `docs/completed/memory.md`).

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

## Calling (PR #4) — parked on `batch/calling`, not on main

Parked 2026-09-25 after review. The integration of #4 against the real
cards is preserved on `reconcile/calling` (one test still failing
there). S1 alone would break the whole device on stage, not just
calling. Fix S1–S4 before #4 merges.

~~**S1 — A missed call jams calling AND the spacebar.**~~ Fixed
2026-09-26 on `cloud/demo`: `CallController` polls `fetch_call(sid)` on
a watcher thread while DIALLING (every 2 s) and tears down on a
terminal status (no-answer, busy, failed, canceled, completed) or after
a 45 s ring timeout, completing the call via REST in the timeout case.
Teardown clears the hold handler, so the spacebar is a spacebar again.
Verified in a headless browser: a tap during the ring does nothing,
a tap after the timeout starts a turn. S2–S4 below are still open.

**S2 — A name one letter off dials without asking.** Confident band is
≥0.93 plus a 0.05 margin (`saathi/call/match.py:43-47`, `:146-147`);
the margin only protects her when both people are saved.
*Repro (measured):* only Deepak saved, she says "call Deepa" → 0.960,
rings with "Calling Deepak." Also Arun→Aruna 0.960, Amit→Amita 0.960,
Mohan→Mohana 0.967, Vijay→Vijaya 0.967, Ram→Rama / Raj→Raju / Jun→June /
Ali→Alia 0.942; Tan→Tang and Chen→Cheng fold to 1.000.
*Fix:* confident only when the folded forms are identical (or equal
length); anything else → unsure → a Confirm card.

**S3 — Two different people with the same name merge.** Relations are
attached by normalised name and moved onto the latest row for that
name; the edge's own `dst` is ignored (`saathi/call/contacts.py:315-324`).
*Repro (reproduced by script):* save Priya +6591111111 as daughter,
then Priya +6592222222 as neighbour → `find_by_relation("my daughter")`
returns the neighbour's number, relations ('daughter', 'neighbour').
"Call Priya" also dials the latest Priya without asking.
*Fix:* resolve a relation through the edge's `dst` entity; latest-wins
only among rows for the same person; ask when a same-name save carries
a different relation.

**S4 — Two people with the same relation: the newest wins, silently.**
`_latest_edges` keeps one `dst` per relation, so a new edge is treated
as a correction (`contacts.py:335-346`, `tools/calling.py:439-442`),
and the relation path dials straight away without the choice flow.
*Repro:* save daughter Priya, later save daughter Anita → "call my
daughter" always rings Anita; Priya is unreachable by relation.
*Fix:* several current edges for one relation → `ChoiceFlow.offer`;
only a same-person re-save replaces an edge.

Minor (same review; details in the PR #4 thread): M1 teardown still
stops parec/pacat on the screen loop; M2 an exception in `audio.stop()`
leaves the controller in IN_CALL (try/finally); M3 a failed REST
hang-up leaves the far end on silence (close the media socket too);
M4 `stream_started` doesn't check callSid, so a late stream from a
previous call can take the live mic; M5 `dial()` holds the lock
through tunnel/REST work and `RelayError` escapes untranslated; M6
pending cards never expire, so a stale "Call Deepak?" can be answered
by an unrelated later "yes"; M7 a mismatched voice answer is reported
to the model as "no card" while the card is still up; M8 the save on a
tapped "yes" runs SQLite on the screen loop and can fail silently; M9 a tap landing between `show()` and registration is lost (fixed
for the media card 2026-09-26: the id is the tool's before `show()`;
calling's flows should do the same).

## Open decisions (waiting on the user)

- ~~**`IdentityStore.retire(table, row_id, at)`**~~ — decided by the
  user and merged 2026-09-25 (PR #2), with the correction tool.
- **Nothing writes `entities` or `edges` at all.** Making `since/until`
  real means building entity resolution from turns first, not just
  filling two columns.
- **SPEC.md Memory-section diff** (three layers, silent turns, real
  prompt-token count) — proposed 2026-09-24, not applied. The user
  applies spec edits.
- ~~**Where a correction is recorded**~~ — decided: a high-importance
  `episodes` row, no new table (PR #2).

## Dev-machine notes

- `pytest` on this box needs `env PYTHONPATH= …` — the shell profile
  leaks ROS Foxy's py3.8 plugins into the venv and pytest dies at
  startup. Not a repo problem; CI never sees it.
- The raw mic on this box has a clipping noise floor (ERLE ~11 dB vs
  the 25–30 dB target). The echo-cancelled source measured clean
  (RMS 1–750) on 2026-09-24, so this may have been the raw path only.
