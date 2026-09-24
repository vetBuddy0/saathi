# Decisions

Retroactive record of calls made without an explicit instruction —
substitutions, defaults, and judgment calls — with one line of reasoning
each. New entries go at the bottom, dated. Superseded entries are struck
through, not deleted, so the history of *why* something changed stays
readable.

---

**2026-09-17 — Substituted `openai/gpt-oss-120b` for Kimi K2 in cascade.py.**
The GROQ_API_KEY on hand doesn't have `moonshotai/kimi-k2-instruct` or
`-0905` (confirmed via `GET /v1/models`); gpt-oss-120b was the strongest
model the key did have, needed to close the loop the same session.
~~Superseded 2026-09-18: neither Kimi K2 nor llama-3.3-70b-versatile nor
qwen/qwen3-32b exist on this account anymore; see the 2026-09-18 model
entry below.~~

**2026-09-17 — Piper as the default/fallback TTS backend, not Kokoro.**
Kokoro pulls `torch` unconditionally — a real cost on first boot and disk
on the Pi this targets — while Piper installs and synthesizes in seconds
with no such dependency; Piper stays wired as the last-resort offline
fallback regardless of whichever backend the comparison eventually favors.

**2026-09-18 — MeloTTS listed as a permanently unavailable backend, not
attempted further.** Verified against PyPI's own metadata: `melotts`
ships no wheels at all (sdist only), and its pinned `torch<2.0` has zero
Python 3.12 wheels on any platform — not installable into this project
as currently pinned, independent of architecture. Kept as a real
`TTSBackend` entry (`available()` always `False` with this exact reason)
rather than deleted, so it stays visible in the comparison instead of
silently missing.

**2026-09-18 — `TTSBackend.preload()` and `KokoroBackend`'s language
gating are additive capabilities, not `TTSBackend` interface methods.**
Discovered via `getattr()`, same pattern as `preload` on `PiperBackend` —
avoids widening the abstract interface for something only two of five
backends need, keeping the "voice/tts" contract itself small.

**2026-09-18 — Google backends open one streaming session per sentence,
not one multi-sentence stream.** Keeps every backend's
`synthesize_stream()` contract identical (one WAV per sentence) instead
of giving Google a special case; the tradeoff is not exploiting
sub-sentence streaming latency within one sentence, which nothing
currently needs since sentence-level chunking is already the
interrupt-responsiveness boundary this package exists for.

**2026-09-18 — GCP IAM role recommended as `roles/cloudtexttospeech.client`.**
Flagged in chat as convention-based, not confirmed against a first-party
role-reference page — the safer, unverified default rather than a role I
was more confident about but had less reason to pick.

**2026-09-18 — Sentence-chunked synthesis checks the interrupt flag both
before requesting the next sentence's audio and before starting its
playback (not just one or the other).** Bounds the un-interruptible
window to roughly one sentence's synthesis time on every backend,
including ones with no streaming of their own (Piper, Kokoro) — the
direct fix for the corrected barge-in understanding (see
`docs/completed/B-barge-in.md`).

**2026-09-18 — `preferences.key`'s `PRIMARY KEY` conflict resolved by
proposing a SPEC.md diff rather than adding an `update()`/`upsert()`
method to `IdentityStore`.** `IdentityStore` is one of CLAUDE.md's five
protected interfaces; a schema change needs a conversation the same way
an interface-surface change would, and the schema fix required zero
change to `create`/`append`/`read`. ~~Superseded 2026-09-18: diff applied
per explicit instruction — see the migration entry below.~~

**2026-09-18 — Added a read-only `IdentityStore.path` property.**
`identity/compile.py`'s background-thread context refresh crashed
reusing the caller's SQLite connection across threads (a real
`sqlite3.ProgrammingError`, found by testing). Opening a second
connection to the same file from that thread is SQLite's own supported
way to do this; exposing `.path` was the minimal, purely additive change
to a protected interface that made it possible, chosen over flipping
`check_same_thread` off on the shared connection (which would have
changed `IdentityStore`'s threading contract for every caller to suit
one background job).

**2026-09-18 — Compiled context refreshes after every `say()` call
returns, not on `start()` or mid-turn.** SPEC.md requires context be
compiled "between turns, never during one" and already held "when she
starts speaking" — refreshing right as the previous turn ends is the
latest point that still guarantees the next turn's context is ready
before a new press can begin, without adding synchronous work to
`start()` on the event-loop thread.

**2026-09-18 — The two-sentence reply cap lives in `compile.py`'s own
appended sentence, not in `persona_stub.txt`.** It's an engineering
constraint (cost and fatigue both worsen with longer replies), not
identity content — `persona_stub.txt` stays the user's file to write,
with no engineering rules mixed into it.

**2026-09-18 — A superseded (barged-into) turn logs nothing to `turns`,
rather than a partial or marked row.** `_log_turn` sits at the exact
point `core.handle(Event("done"))` already only fires for turns that
weren't superseded — reusing that boundary was simpler and more
correct than inventing a new "was this superseded" signal for the log
line alone.

**2026-09-18 — `turns.mode` is hardcoded to `"voice"`.** SPEC.md names no
other mode and nothing in this codebase produces one; a real second
mode (e.g. text input) can pick its own value later without this needing
to have guessed right about a mode that doesn't exist yet.

**2026-09-18 — Fixed Piper's voice download to use `sys.executable`
instead of the literal string `"python3"`.** Found by testing, not
inspection: on this machine a bare `python3` resolved to an unrelated
Anaconda interpreter with no `piper` installed. Existing, not something
introduced this session — fixed rather than left once found, since it
silently breaks the very thing `setup-pi.sh`'s own download step already
does correctly via `uv run python3 ...`.

**2026-09-18 — `set_language`'s permission scope is named `"preferences"`,
a new scope not used by any existing tool.** `call_contact`/`play_music`
use `"calls"`/`"music"`; a device-configuration change is a different
category of action from those (no external-world consequence), so it
gets its own scope rather than being folded into an existing one that
doesn't semantically fit.

**2026-09-18 — `set_language`'s `"preferences"` permission is granted
unconditionally in `cli.py`, with no elevated consent gate.** Unlike
calls/music (explicitly "out" for v1, real-world consequential actions),
changing which language Saathi replies in has no external effect and no
reason to be gated behind something calls/music-style tools would need.

**2026-09-18 — Tool-calling handles only the first `tool_call` in a
response, not multiple.** `set_language` is the only real tool this
project has; nothing exercises multi-tool-call turns, and building
orchestration for a case with no real tool to trigger it would be
speculative. Flagged in code as a real, unhandled limitation, not
silently dropped.

**2026-09-18 — `Tool` -> LLM function-calling schema conversion lives in
a separate `tools/llm_schema.py`, not as a `description` field on `Tool`
itself.** `Tool` is one of the five protected interfaces; adding a field
for the sole benefit of one caller (an LLM's tool-selection prompt) was
avoidable by keeping descriptions external to the dataclass.

**2026-09-18 — A supported stored language preference now pins the
reply language across every following turn, overriding live detection,
until changed again — reversing the original "detection always wins"
design.** Verified live: the original design made "speak to me in
Mandarin" invisible the instant she next spoke a sentence Whisper
detected as English, which is the common case, since the request itself
is usually made in whatever language she was already speaking. SPEC's
own "effective next turn" language means every next turn, not "until
she next speaks the old language" — indistinguishable from not switching
at all. An *unsupported* preference is still ignored outright, unchanged
from the original Portuguese-reply-bug fix.

**2026-09-18 — The set_language tool's result includes a plain-language
`"note"` explaining the switch takes effect next turn.** Found live:
without it, the model didn't know the switch had already succeeded and
phrased a reply that sounded like a refusal ("I'll continue in English
as instructed") for a request it had actually just granted.

**2026-09-18 — `qwen/qwen3.8-27b` set as the default chat model,
replacing `openai/gpt-oss-120b`.** Explicit decision, not mine to reason
independently, but recorded here for the trail: the only one of four
real candidates that didn't fabricate either a memory or weather data,
fastest, and its token usage tracks what it actually says instead of
hiding a reasoning-token cost multiplier. `gpt-oss-20b` inventing a fake
memory about a daughter's visit is disqualifying for a device talking to
someone with memory problems.

**2026-09-18 — `preferences`/`turns` schema diffs applied directly to
SPEC.md and `IdentityStore`, migrating existing rows.** Explicit
instruction, reversing the general "propose, don't apply" rule for these
two specific diffs only; checkpoint 3's own schema questions are still
proposed-only, per the same instruction.

**2026-09-19 — Added an Autonomy section to CLAUDE.md.** Explicit
instruction, exact text given this time — the first request had no
section text after the colon, flagged and left alone rather than
invented (see the CLAUDE.md commit message for the earlier gap).

**2026-09-19 — TTS pipelining uses a daemon `threading.Thread` + a
one-item `Queue`, not a `ThreadPoolExecutor`.** An executor used as a
context manager blocks on `shutdown(wait=True)` for in-flight work when
the `with` block exits — exactly wrong for an abandoned prefetch after
an interrupt, which needs `_speak()` to return immediately, not wait for
a discarded Piper call to finish. A plain daemon thread is simply
abandoned instead, costing nothing.

**2026-09-19 — Pipelining prefetches at most one sentence ahead, never
more.** The next prefetch only starts once the current one is consumed
from its queue, which an interrupted turn never reaches — bounds
wasted/speculative synthesis to one sentence, matching the existing
barge-in tolerance ("at most one already-in-flight sentence plays out")
instead of introducing a new, larger slack.

**2026-09-19 — `CascadeSession.__init__` warms the voice unconditionally
at construction, regardless of whether an `identity_store` was given.**
The cold-start cost this fixes (a fresh process's first reply) exists
whether or not memory is wired in; tying it to `identity_store` would
have made the fix depend on an unrelated feature flag.

**2026-09-19 — The initiative dry run simulates two ticks per day
(~09:00, ~21:00), not one.** A single daily tick made reminders due
later the same day only get caught the *next* day's tick (a simulation
artifact, not a scheduler bug) — twice-daily ticks catch same-day
reminders realistically without needing to simulate the much higher
real tick frequency a live scheduler would actually run at.

**2026-09-19 — The dry run's restraint demonstration (quiet hours /
unconfirmed presence / busy) is a direct, supplementary call to
`policy.should_speak()`, not woven into the seeded week's own timeline.**
None of the seeded week's ticks happened to land during a suppressed
context; adding one artificially into the week's own narrative would
have meant inventing an implausible scenario (a reminder due at 2am) to
force it — a direct call on the same real candidate, real code, is more
honest than bending the synthetic week to manufacture a result.

**2026-09-19 — Scoring: a due reminder always outranks a "noticed"
candidate (`score = inf`); among "noticed" candidates, `reflect.py`'s
own `confidence` breaks ties.** Medication over a comment about her
mood is the obvious priority ordering and needed no new number invented
for it; confidence already existed and measures exactly "how grounded
is this," the right axis for ranking insights against each other.

**2026-09-19 — A candidate is "resolved" (stops being re-proposed by
`scheduler.py`) only if it fired or expired — every other suppression
reason (gated, capped, cooled down, outscored) is retried next tick.**
This is the literal mechanism "the rest stay as candidates, not a queue
to be drained" required: without it, a losing candidate would either
never be reconsidered (the old, buggy behavior) or need a separate
"pending" table this schema has no room for.

**2026-09-19 — The daily cap blocks reminders too, exactly as
instructed ("whatever the score"), with no invented exemption.** A real
tension (a medication reminder could get capped by three unrelated
"noticed" utterances earlier the same day) is flagged plainly in
`docs/initiative-dry-run.md` rather than silently carved out — the
instruction gave reminders one specific exemption (cooldown), not a
blanket one, and adding a second on my own judgment would be deciding
"what changes what she hears" without being asked.

~~**2026-09-19 — The daily cap blocks reminders too, exactly as
instructed ("whatever the score").**~~ Superseded the same day: the
instruction was withdrawn after the tension was flagged. Reminders are
now their own lane (SPEC.md, "Initiative") — never capped, never cooled
down, never expired, never counted against the social budget; every due
reminder fires. One crossover: a reminder firing resets the social
cooldown.

**2026-09-19 — The presence/quiet-hours/busy gate applies to the
reminder lane too, non-terminal.** The two-lane instruction named cap,
cooldown and expiry as what reminders escape — not the base gate.
Reminding an empty room helps no one, and a held reminder returns next
tick rather than being dropped, so nothing is lost by gating it.
Reversible in minutes if quiet hours turn out to swallow a late dose;
the test `test_a_reminder_is_still_held_when_presence_is_unconfirmed_but_not_dropped`
pins the current behavior so the choice is visible, not accidental.

**2026-09-19 — Every due reminder in a tick fires; they don't compete
for one slot.** Each is its own obligation. "One per tick" is the social
lane's rule, built for conversational restraint, and applying it to two
simultaneously due medications would make one wait on the other for no
reason anyone asked for.

~~**2026-09-19 — The presence/quiet-hours/busy gate applies to the
reminder lane too.**~~ Refined the same day, explicit instruction:
reminders respect presence and *ignore quiet hours* — if someone set a
reminder for 10pm, 10pm is the point. `busy` is still applied to
reminders (held one tick during a call, never dropped); the instruction
named presence and quiet hours only, and interrupting a phone call with
a tablet reminder wasn't asked for. Reversible in minutes.

**2026-09-19 — Phrasing claims are flagged regardless of whether the
source contains the same construction.** Found by the test for the
dry run's own fabrication: a source saying "Priya is planning to visit
Saturday" was licensing "Priya is planning to call this evening" — the
construction matched, the fact didn't. Recalling a stated plan now has
to be phrased without future tense or it falls to the template.
Stricter than strictly necessary, on purpose: a false positive costs a
stiff sentence; a false negative costs her an evening waiting for a
phone that never rings.

**2026-09-19 — The phrasing check is a local heuristic, not a second
model call.** Grading one model's output with another would be a
second place for a fact to be invented, and would put a model call in
the aftermath of every tick. Capitalized-word and pattern matching is
crude and occasionally forces a template on an innocent sentence —
the intended failure direction.

**2026-09-19 — "Noticed" dedup keys on the rule's text, not a rule id.**
`initiatives` has no `rule_id` column; adding one is a schema question
(proposed with the many-to-many provenance diff, not applied). The
rule's text is exactly what a "noticed" row's `reason` holds, so it's a
real key today, not a placeholder — two rules with byte-identical text
would collapse, which is acceptable until the column exists.

**2026-09-19 — Stopped tuning Piper's latency; the 1213ms-vs-1200ms
budget stays red, reported honestly, until Google's streaming TTS is
unblocked.** Explicit instruction: further Piper-specific work would be
thrown away the moment Google's credentials land, since that's the
architectural fix (audio starts before the sentence finishes rendering,
not just synthesized faster on the same blocking path). Pipelining and
the startup warm-up were kept — they help any backend, not just Piper.

**2026-09-24 — The silence guard gates on Silero VAD before STT, not
on Whisper's `no_speech_prob`.** The instruction named `no_speech_prob`.
Measured on this machine, four consecutive probes of a silent
echo-cancelled source: `whisper-large-v3-turbo` returned
`no_speech_prob=0.0000` every time while transcribing the silence as
" Thank you.", " I'm going to go." and " voice. That is me. Thank you."
A threshold on a value that is always zero is a guard that never
fires. The same buffers scored 0 of 119 chunks over Silero's threshold
(max 0.37), so the question "did she say anything" is asked of the
audio, in `audio/vad.py`'s `contains_speech()`, before an STT call is
spent. The empty-transcript half of the instruction is kept as a second
guard behind it.

**2026-09-24 — A silent turn writes no `turns` row.** `turns` feeds
the latency-budget p95. A turn that skipped STT, LLM and TTS entirely
has no timings to contribute and would drag that percentile down for
reasons that have nothing to do with how fast she answers. The turn
ends via `no_response` -> IDLE, logged as a line, not a row.

**2026-09-24 — Window token budget uses a 4-chars-per-token estimate,
not a tokenizer.** Any real tokenizer is a new dependency; CLAUDE.md
rules that out for this. The estimate only decides when to evict one
exchange from the window, where being off by a fifth is one exchange
either way, and the exact count from the API is what's logged per
turn — nothing downstream trusts the estimate to be true.

**2026-09-24 — One background digest call per turn, not two.**
Importance rating (layer 3) and summary regeneration (layer 2) read the
same material; two calls would send it twice, every turn. Asked for
together, returned as one JSON object, from `say()`'s tail thread —
never during a turn. Only spent when there is somewhere for the output
to go (a store, or evicted exchanges to fold); sessions with neither
make no extra call.

**2026-09-24 — Importance stays on Park et al.'s 1-10 scale.**
`retrieve_episodes` min-max normalizes across the candidate set, so the
absolute range never reaches the ranking; keeping the paper's numbers
lets the prompt say what the paper says.

**2026-09-24 — Episodes are written with `embedding` NULL, and that
leaves retrieval's relevance axis dead. Raised, not hidden.** Groq
offers no embedding model; CLAUDE.md rules out a vector DB and a
second vendor. `retrieve_episodes` already documents this degradation
(recency + importance only), so the rows land on a path that exists
rather than a new one — but it means "relevance" in SPEC.md's
"recency + importance + relevance" is currently a constant. Needs a
decision on an embedding source; not one this pass can make.

**2026-09-24 — Fixture wiring in `tests/test_cascade.py` gained
`speech_gate=_hears_speech`.** Not "changing a test so it passes":
the constructor grew a dependency whose real default (Silero) correctly
calls the fixtures' 200 bytes of zeros silence. Tests inject the gate
the same way they already inject `client=` and `backends=`; one test
deliberately omits it to pin that the real gate is the default.

**2026-09-25 — `IdentityStore.retire(table, row_id, at)` is the fourth
verb.** The user's decision, not mine — recorded for the trail: one of
the five interfaces changed, the conversation happened, the answer was
yes. One narrow "this stopped being true" primitive: `active = 0` on
`rules`/`reminders`, `until = at` on `edges`. Not a general `update()`
(no column name, no value), and it refuses tables with nothing to
retire (`NotRetirable`) and ids that match nothing (`UnknownRow`)
rather than no-op. The option that lost — append-only event tables
per flag with readers computing effective state
(`docs/completed/checkpoint-3.md`, option b) — kept the interface
untouched at the cost of three more tables and read-side logic in
every consumer. An append-only store that cannot be corrected is
worse than one that forgets.

**2026-09-25 — `retire`'s `at` is written only for `edges`.** `rules`
and `reminders` have no column to hold when they were retired, and
adding `retired_at` is a schema question for SPEC.md, not something
the primitive invents. The correction tool's `episodes` row carries the
timestamp for the one path that retires today. Kept in the signature
so the primitive is uniform and the column can land without changing
callers.

**2026-09-25 — `retire("edges", ...)` keys on SQLite's implicit
`rowid`, and nothing can obtain one through `read()`.** `edges` has no
primary key in SPEC.md's schema and `read()` is `SELECT *`, which never
returns `rowid`. Implemented and tested so the primitive is complete;
the gap is stated plainly rather than papered over with a `rowid` in
`read()`'s output (a change to what every table's rows look like, for
a table nothing writes yet).

**2026-09-25 — A correction is recorded as an `episodes` row at
importance 9, not in a new `corrections` table.** The user's decision
(TODO.md's open item is thereby closed). `episodes` is what retrieval
reads and what `reflect.py` reflects over; a correction anywhere else
would be invisible to both. The row's text names what she said, what
is true if she said, and the belief that was retired — a plain
sentence, so the next turn's model reads the negative fact as well as
the positive one.

**2026-09-25 — The correction tool always writes the episode, even
when no rule matched.** A wrong belief can live in an `episodes` row
(the digest writes observations straight from what she said), and
nothing can retire an episode. Her own correction landing in the
context at importance 9, next to the wrong observation, is the only fix
that path has. Recording nothing when no rule matched would leave the
wrong episode unanswered.

**2026-09-25 — Active rules reach the prompt as `[memory N] sentence`,
plus one sentence saying the numbers are for `correct_memory` only and
never to be said aloud.** The model has to name *which* belief she
said was wrong, and `cascade.py` honours one tool call per turn, so a
"list rules, then retire one" two-step cannot happen inside a turn.
The number is the rule's `id` — the one piece of storage that reaches
the prompt on purpose (it is an address, not a float). The option that
lost: a `list` action on the tool, useless under the single-call limit.
Reversible in minutes if a number is ever heard aloud; the instruction
sentence is the guard.

**2026-09-25 — Without a `rule_id`, the tool matches content words of
`believed` (the model's quote of the wrong thing) then `said` against
active rules; any shared word is a match, ties go to the newest rule,
no overlap retires nothing and asks the model to ask her.** A fuzzy
miss costs one short question; a fuzzy hit on the wrong rule would be
a second wrong belief, so the fallback stays behind the numbered path
rather than replacing it.

**2026-09-25 — `correct_memory`'s permission scope is `"memory"`, a
fourth scope after `calls`/`music`/`preferences`.** Changing what the
device believes about her is its own category of action — not a device
setting, not an external effect. Granting it in `cli.py` is
cross-territory and written up in `docs/completed/memory.md`.

**2026-09-25 — The correction handler opens its own connection from
`store.path`, never the shared one.** It runs inside `end_turn()` on
the screen server's executor thread; the shared connection belongs to
the thread that opened it. The `threadsafe_reader` pattern
(`identity/preferences.py`), because the alternative passes every test
and raises `sqlite3.ProgrammingError` on the first real correction —
commit c4d3717 was that bug. A test calls the handler from a second
thread to pin it.

**2026-09-25 — The correction episode is written with `embedding`
NULL, and `backfill_embeddings` fills it later.** The handler is the
hot path — inside a turn — and a model may not load there. NULL is the
documented degraded state (`retrieve_episodes`), and importance 9
carries the row until the vector arrives.

**2026-09-25 — Local embeddings: `all-MiniLM-L6-v2` via `onnxruntime`,
not `sentence-transformers`, and a pure-Python WordPiece tokenizer,
not `tokenizers`/`huggingface-hub`.** Not a vendor (model files fetched
once by plain HTTPS from huggingface.co, Apache 2.0, verified live
2026-09-25: `license:apache-2.0`, `onnx/model.onnx` 90,405,214 bytes,
`vocab.txt` 231,508 bytes), not a vector DB (brute-force numpy cosine,
as SPEC.md allows), so CLAUDE.md's "Never" list is untouched.
`sentence-transformers` pulls `torch`, an aarch64 problem twice already.
`onnxruntime` (MIT) is now an explicit line in `[project.dependencies]`
rather than riding Piper's transitive pin — `uv lock` changed exactly
two lines; the wheel set already includes `manylinux_2_28_aarch64`.
`tokenizers`/`huggingface-hub` would each have been a new direct
dependency for ~60 lines of BERT WordPiece and one `urllib` call.

**2026-09-25 — An English-only embedding model, on purpose.** What is
embedded is `digest.py`'s observation ("She mentioned her scan is on
Thursday.") and the correction tool's sentence — English third person
regardless of the language she spoke, by that prompt's own design. If
a non-English episode writer ever appears, the model files are the only
thing to swap.

**2026-09-25 — `embed()` never downloads; the model is fetched only by
an explicit `--download`/`--selfcheck`/`--backfill`.** With the files
absent the embedder returns `None` and everything degrades exactly as
before (relevance 0) — the same shape as a TTS backend's
`available()`. The option that lost, fetching on first use from the
between-turns thread, would start a 90 MB download mid-conversation
with nothing on screen to explain it. `scripts/setup-pi.sh` should
gain the download step (cross-territory, written up).

**2026-09-25 — `compile_context` uses the newest episode's stored
vector as the retrieval query when no caller passes one.** "Relevant to
what was just discussed", one turn stale like everything else there,
and zero model calls inside `compile_context` — which also runs on
`CascadeSession`'s constructor thread and must stay pure computation.
Passing the live utterance's vector from `cascade.py` is better and is
written up as the cross-territory change; the parameter already exists.

**2026-09-25 — `backfill_embeddings` writes one guarded `UPDATE`
against `store.path` directly, rather than adding a second primitive
to `IdentityStore`.** Filling a NULL cell exactly once (`WHERE embedding
IS NULL`, never overwriting) is not a correction and not a change to
anything that was ever believed, and the user had just drawn the line
at one narrow primitive, not an `update()`. The `identity/__init__.py`
rule "only `store.py` touches SQLite" now records this single
exception. Reversible in under an hour if the user would rather have a
`fill_null(table, row_id, column, value)` on the interface — the SQL
moves, nothing else changes.

**2026-09-25 — `tests/conftest.py` points the default embedder at an
empty directory for every test.** `tests/test_cascade.py` reaches
`write_episode` through the real post-`say()` path; on a machine that
has run `--selfcheck` that test would load the real model and store
real vectors, which is a test whose behaviour depends on what a shell
command left on disk. One autouse fixture makes every machine behave
like CI. Tests that want vectors inject a fake embedder.

**2026-09-25 — Two existing tests changed expectation, and this is
said plainly rather than done quietly.** `tests/test_identity_profile.py`
pinned "retract_rule raises RetractionNotSupported and `active` stays
1" — true then, false by design now; it asserts the retraction instead.
`tests/test_identity_digest.py`'s write test pinned "embedding is None
as a deliberate gap" — the gap is closed; it now asserts NULL only as
the no-model degraded state that CI and `conftest.py` guarantee.
Neither is "changing a test so it passes": the code's behaviour changed
on the user's instruction, and the tests describe the new behaviour.
`RetractionNotSupported` itself is deleted — dead code that lied.

**2026-09-25 — ONNX session uses one intra-op thread.** One sentence
at a time, between turns, on a Pi that is also doing audio: 14 ms for
three sentences warm on this machine, and not contending with playback
for cores matters more than shaving that.

**2026-09-25 — Found, reported, not retuned: under equal-weight
min-max, relevance alone rarely beats recency + importance combined.**
Live against a six-episode scratch store, "how does she take her tea"
gave the tea episode cosine 0.69 against ≤0.34 for everything else
(normalised relevance 1.00 vs ≤0.39), which moved it from last (not
sent at top_k=5) to fourth (sent) — real, but it still trailed the
newest episode's recency 1.00 + importance 0.50. Min-max stretches a
five-hour recency spread to the full [0, 1]. Weights and normalisation
are `compile.py`'s documented equal-weight choice ("tuning without real
usage data would be guessing"), and changing them changes what she
hears — left as is, noted for the user.

**2026-09-25 — An explicit `rule_id` that matches no active rule
records the correction and asks; it never falls through to the fuzzy
matcher.** Found by code review: the model's context can carry a
number retired last turn, and her words then share a word with a
*different* active rule — retiring that one is a second wrong belief,
the exact thing the module says is unacceptable. The fuzzy path is
only taken when no number was given at all. `rule_id` is coerced with
`int()` first, because models emit `"3"` as often as `3`.

**2026-09-25 — The correction episode is written before the rule is
retired.** Two commits through the interface, no single transaction
available without reaching into the connection. If the second write
fails, a recorded correction with its rule still active is the lesser
failure: her words reach the next turn's context either way, and a
retired rule with no record of why is the trail-less change this tool
exists to prevent.

**2026-09-25 — Retired rules are sent to the reflection pass as "known
wrong".** Found by code review: the retired rule's source episode is
still in `episodes`, and the correction episode names the belief, so
the next `reflect()` would re-derive it as a fresh active rule. Told as
sentences (never ids) in `_INSIGHTS_PROMPT`. The option that lost: a
deterministic post-filter on word overlap with retired rules — it
would also drop the corrected version ("visits on Sundays" vs a retired
"visits on Saturdays"), which is the rule that should be learned next.

**2026-09-25 — Model downloads are pinned to a HuggingFace commit and
checked by sha256, not `main` and a byte count.** Found by code review:
an upstream re-export would have made every fresh install fail
permanently against a size pinned in code. `resolve/<sha>/` cannot
change; the model's sha256 equals HuggingFace's LFS etag for that
commit, the vocab's was computed from the verified download. A 60 s
socket timeout so a stalled Wi-Fi link fails the install step instead
of hanging it.

**2026-09-25 — `retire("edges")` uses `COALESCE(until, ?)`; a damaged
or foreign-dimension embedding blob scores 0; an inference exception
returns `None`; backfill embeds in chunks of 64.** All from code
review, all the same shape: a second retire must not move a
relationship's end date if the primitive claims idempotence; one bad
row must not stop `compile_context` on the constructor thread; a model
that loads but fails at `run()` must not kill the post-`say()` thread
that also recompiles context; a first backfill over a long history
must not build one table-sized tensor on a Pi.
