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

**2026-09-25 — The Google Neural2/WaveNet backend synthesizes per
sentence through the batch API; only Chirp3-HD streams.** Found on
the first live run, not by reading: `streaming_synthesize` with any
non-Chirp voice returns `400 Currently, only Chirp 3: HD voices are
supported for streaming synthesis`, and the first-party streaming page
says the same. The brief asked for both "streaming, not batch" and a
Neural2 comparison; on this API they're mutually exclusive. Kept the
backend (batch per sentence, the same shape as Piper and Kokoro, with
"per-sentence" in its display name) so the comparison could be run at
all. The option that lost — deleting it — would have made "is Chirp
worth its price" unanswerable.

**2026-09-25 — Mandarin and Bengali on the Neural2 backend are WaveNet
voices.** `list_voices()` at the Singapore endpoint: `cmn-CN` and
`bn-IN` have no Neural2 voices, only WaveNet and Chirp3-HD. WaveNet was
already "an alternative voice ID within the same class" (2026-09-18),
so a per-language table picks Neural2 where it exists and WaveNet where
it doesn't, all female. Measured live: WaveNet Mandarin takes ~3 s to
first audio (3077 ms and 3023 ms on two runs), which by itself rules
this backend out for a Mandarin speaker.

**2026-09-25 — Chirp3-HD's voice table is one speaker name applied to
every language code.** Chirp's names (`Achernar`, `Sulafat`, ...) are
the same speaker in `en-US`, `cmn-CN`, `hi-IN` and `bn-IN` — the
structural answer to "recognisably the same character" that no other
backend in the registry can give. Default speaker `Sulafat`: female,
and the one whose descriptor in Google's own voice list is "Warm", the
brief's word. `Achernar` ("Soft", the blind draft's pick), `Gacrux`
("Mature") and `Vindemiatrix` ("Gentle") are rendered as candidates by
`compare.py --candidates`; the final choice is the user's ears, and it
is one constant.

**2026-09-25 — Chirp's streamed PCM is joined and wrapped in a WAV
header per sentence; sub-sentence yields were rejected.** `TTSBackend`
promises one WAV per sentence and `cascade._speak()` spawns one
`paplay` per item. Yielding each ~240 ms Google chunk as its own WAV
would put a process spawn inside every sentence — audible gaps, worse
than the sentence-level wait. The first-chunk win (measured: first
chunk at ~330 ms vs ~720–830 ms for the whole first sentence) needs a
raw-PCM playback path in `audio/playback.py` and `cascade._speak()`,
outside this stream's territory. `GoogleChirp3HDBackend.stream_pcm()`
is the additive, `getattr`-discovered capability that path would
consume (the `preload()` pattern, 2026-09-18); the diff is proposed in
`docs/completed/voice.md`, not applied.

**2026-09-25 — Google client moved from `texttospeech_v1beta1` to
`texttospeech_v1`.** Both expose `streaming_synthesize` at the pinned
2.37.0; the GA surface is the one less likely to move, and the beta
import had no recorded reason.

**2026-09-25 — An unsupported language raises `ValueError` from the
Google backends rather than defaulting to English.** Same principle as
`KokoroBackend` and `voice/language.py`: unavailable and visibly so
beats silently wrong. `cascade` only ever passes a resolved, supported
language, so nothing on the real path can hit it.

**2026-09-25 — `compare.py` measures TTFA exactly as `cascade._speak()`
does (generator, then clock, then first `next()`), and reports Google's
first-chunk time in a separate column labelled a projection.** A number
taken any other way wouldn't be the one `turns.first_tts_chunk_ms`
holds and the latency-budget test reads. The projection column is
there because it is the number that decides whether the playback-path
change is worth making; it is never presented as today's TTFA.

~~**2026-09-25 — `tts_backend` preference on this machine's
`~/.saathi/identity.sqlite3` set to `google-chirp3-hd` for the barge-in
proof, and left set.** This changes what she hears on the next run, so
it is called out here and in `docs/completed/voice.md` rather than
done quietly: the brief's whole point was to hear the warmer voice,
and the Ctrl+L panel reverts it in one click. Piper remains
`DEFAULT_BACKEND_ID` and the automatic fallback whenever Google is
unavailable — the repo's default is unchanged.~~
Superseded the same day, after code review: CLAUDE.md lists "anything
that changes what she hears" as ask-first, and until `cascade.py`
forwards prefetch exceptions (diff proposed in `docs/completed/voice.md`)
a Google failure costs a sentence. Preference reset to `piper`; the
Ctrl+L panel switches to Chirp in one click, and that click is the
user's.

**2026-09-25 — A Google synthesis failure yields 100 ms of silence for
that sentence and takes the backend offline for 60 s, rather than
raising.** Found by code review, not by me: `cascade._prefetch_next_chunk()`
only enqueues on success, so a raise from the backend leaves `_speak()`
blocked on its queue forever — `say()` never returns and the session is
dead. Silence is not the preference; it is the honest degradation
until the helper forwards exceptions (proposed, not applied). The
failure is logged at WARNING, and the cooldown makes `available()`
false so `_current_backend()` routes the next turn to Piper through
the path that already exists — `available()` only stats the key file
and cannot otherwise see a revoked key, exhausted quota or a Wi-Fi
drop.

**2026-09-25 — Every Google call carries a 10 s deadline.** The
streaming call has no default deadline in the gapic client; a stalled
connection would hold a sentence, and the turn, open indefinitely. Ten
seconds against a measured 0.5–1.7 s per sentence.

**2026-09-25 — A "voice" is a pair: the same speaker in every supported
language, six entries at most, in `voice/tts/voices.py`.** The user's
rule, and checkpoint 2's exit condition: pick a voice and she must not
become a different person when she switches language. Chirp3-HD is the
only backend in the registry that can offer that structurally (one
speaker name across `en-US`/`cmn-CN`/`hi-IN`/`bn-IN`), so five of the
six are Chirp speakers named by Google's own descriptors — Warm
(Sulafat, the default), Soft (Achernar), Gentle (Vindemiatrix), Mature
(Gacrux), Bright (Zephyr); all confirmed live, female, in all four
locales. The sixth is Piper, listed as the offline fallback, not as a
character. **Neural2 is not a voice**: Google has no Neural2 Mandarin
voice, so that backend is `en-US-Neural2-C` + `cmn-CN-Wavenet-A`, two
different people, and cannot meet the rule. It stays in the panel's
backend row with its list price so the Chirp-vs-Neural2 cost comparison
the user asked for is still visible; the brief's "compare with Neural2"
is met there and not as a selectable voice. The option that lost:
keying the preference by backend id plus a speaker string — that puts
the pairing rule in JavaScript and lets a stray `tts_backend` row select
a backend with no pair. The old `tts_backend` preference is still read,
as a fallback, so a device that picked Chirp before voices existed keeps
hearing Chirp.

**2026-09-25 — Per-voice cost comes from three new `turns` columns
(`voice`, `tts_chars`, `tts_cost_usd`), not from a list price.** The
panel's backend row showed `$30 / 1M chars` and nothing else; "as with
the backends" in the brief assumed a real-usage figure that was never
wired (`docs/completed/C-tts-backends.md`). `cost_usd` stays the LLM
half; nothing sums the two yet. Rows logged before the columns existed
have `voice` NULL and are not attributed to anyone — "spent so far"
means since the picker, and the panel says so rather than guessing.
Added in place by `ALTER TABLE ADD COLUMN`; the aggregation is a Python
fold in `identity/usage.py` (`IdentityStore.read` is exact-match only
and adding SQL aggregates to one of the five interfaces is a
conversation, not a feature). SPEC.md's `turns` line changes; that diff
is proposed in `docs/completed/voice-picker.md`, not applied.

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

**2026-09-25 — `"music"` is granted in `cli.py` (proposed diff), reversing
the 2026-09-18 "calls/music are out for v1" gate.** Explicit instruction:
the YouTube stream's brief makes music real. The 2026-09-18 reasoning
("real-world consequential") still holds for calls — a call reaches
another person — but playing a song on her own screen has no consequence
outside the room, and stopping it is one word. Granted unconditionally,
same as `"preferences"`; `"calls"` stays ungranted. The grant itself is
a `cli.py` edit (cross-territory), written into
`docs/completed/youtube.md`, not applied here.

**2026-09-25 — One `play_music` tool with an `action` enum, not ten
tools.** `cascade.py` handles exactly one tool call per turn, so
"search, then offer" has to be one call whose result carries the
titles. Ten small tools would have put ten descriptions in front of the
model on every turn; one tool whose vocabulary matches how she talks
(`louder`, `bigger`, `again`, `next`) is what the real model
(`qwen/qwen3.8-27b`) picked correctly on the first try. Name kept as
`play_music`, permission kept as `"music"`, exactly the stub's — SPEC.md's
"v2 swaps an implementation rather than inventing plumbing".

**2026-09-25 — Search results live in the tool's controller, not in the
model's context or the identity store.** "The second one" three turns
later must resolve to the same video whether or not the model still has
the list in its window; a Python list on the object the handler closes
over is the only place that is true by construction. Not persisted:
what was offered is conversation state, not memory, and a reboot
forgetting it is right.

**2026-09-25 — Space during playback ducks the video to 20%, held
through thinking and speaking, restored at idle. Never pauses.** The
brief rules out pausing. 20% over mute because a room going
dead-silent on every press reads as broken; 20% over 50% because the
AEC does not cover browser audio on this box (measured — see
`docs/completed/youtube.md`), so whatever is left under her voice is
what Whisper hears. Lives in the browser (`media-policy.js`) keyed on
the `state` messages it already receives, so no PLAYING state was added
to `core.py` — playback is media state, not conversation state.

**2026-09-25 — A new search stops whatever was playing.** The offer has
to be read aloud, and reading three titles over a song she has just
asked to replace serves no one. Pausing-then-resuming-if-she-declines
was the alternative and was rejected as state the model would have to
reason about across turns; "carry on" after a search means "start that
one again", which the tool does.

**2026-09-25 — The screen server owns the media broadcast seam; the
tool never sees the socket.** `build_app(media=...)` installs a
thread-safe callable on the controller at startup
(`loop.call_soon_threadsafe`), because the handler runs in the executor
thread inside `end_turn()`. The alternative — the tool importing the
server and appending to its socket set — is the "tool reaching into the
UI" CLAUDE.md names. `media` messages are never sent on connect; the
existing `state`-then-`settings` handshake is untouched.

**2026-09-25 — Volume is tracked in the tool (70 default, steps of 15,
floor 10, ceiling 100), not read back from the player.** Deterministic
and testable; "quieter" never reaches silence ("stop" is the word for
that); the browser applies the duck on top.

**2026-09-25 — `media-policy.js` is tested in a headless Chromium, not
node.** No node on the device or the dev box, and a JS runtime as a dev
dependency for two pure functions is the kind of thing that turns into
a build step. The face already runs in Chromium; the test skips (not
fails) where no Chromium binary exists.

**2026-09-25 — Fullscreen keeps the face at 22vw × 22vh in the
bottom-left corner, over the video.** The face never goes away is the
brief's rule; a corner over the picture was chosen over shrinking the
video to leave a strip, because a letterboxed 16:9 at 1080p already
has empty bars and a small face over the picture reads as the same
person stepping aside, not a different screen.

**2026-09-25 — Cards are a `screen/` module (`cards.py` + `cards.js`),
not a tool and not part of any one stream.** Every stream that asks
her something (calling, music, reminders) must ask the same way or she
learns three dialects of "which one?". Plain HTML/CSS, no component
library: none is built for a 75-year-old across a room, and the target
sizes, no-hover and one-at-a-time rules would be fought rather than
given. Cards are content and interaction, not the status text SPEC.md
forbids — recorded in the module docstring so it isn't re-litigated.

**2026-09-25 — A fourth option raises `TooManyOptions`; nothing
truncates.** "More than that, say so and offer the best three" needs
the caller to pick the three and to say so out loud; a silent cut would
hide both. Same for `choice()` with one option: that's a `confirm()`.

**2026-09-25 — `CardController.ask()` exists but must not be called
inside `end_turn()`.** Blocking the turn keeps the state machine in
THINKING with the mic closed, so she could only answer by tap —
breaking "voice and touch always both work". Tools `show()` and return
the card's `spoken` text as their note; her spoken answer arrives next
turn and the tool calls `answer(id, ..., source="voice")` — the same
door a tap uses. `ask()` is for code between turns (initiative, a
call's own loop). Kept rather than dropped because the coordinator's
brief asked for it and the between-turns use is real.

**2026-09-25 — A replaced or cleared card is answered as a dismiss
(`source="code"`), never silently dropped.** One card at a time means
a `show()` can pull a question out from under an `ask()`; releasing the
waiter with a dismiss is what keeps "nothing waits on a question she
can no longer see" true.

**2026-09-25 — While a hold handler is set, `core.py` never hears the
press.** A short press does nothing at all (no turn, no capture); a
hold past `seconds` fires once. The alternative — passing the press
through and starting a turn as well — would open the mic over a live
call. The timer lives in `server.py` (it owns the loop) and ticks at
100 ms; `HoldController` only knows the progress and whether it fired.
Progress is re-issued as the same card id so the browser updates the
bar in place.

**2026-09-25 — Card tap targets are 112px tall, text floors are
40/36/32px, digits are grouped in threes.** The brief's minimums are
100px and 32px; the extra is margin so a rounding or a font swap on
the Pi can't drop under them. The floors are asserted from computed
styles in a real 1080p headless Chromium, not from the CSS text.
Contrast pairs are named tokens in `:root` so the AAA test reads the
same values the page does.

**2026-09-25 — With a `CardController`, a YouTube search offers its
three results as a Choice card and the media panel's own results view
is not drawn.** Two copies of the same three lines beside the face is
the dense list the brief rules out, and the card is strictly more: the
same numbers, tappable, dismissable, spoken from its own text. The
panel's results view stays for a screen without cards (and the
existing tests), which is why `MediaController(cards=None)` keeps the
old behaviour byte for byte.

**2026-09-25 — Tap and voice both reach `_play` through the card, and
only a tap starts playback from the card's callback.** The tool
answers its own card with `source="voice"` when she says a number, so
the callback can tell the two apart and not start the same video
twice. A tap is the user gesture the browser's autoplay rule wants; a
`play` message that follows it is "from the interaction", never from
a timer. "Never mind" (spoken → the `never_mind` action; tapped → the
card's dismiss) clears the card and leaves the results referenceable —
"actually, the second one" a moment later still works.

**2026-09-25 — `tools/media.py` imports `screen/cards.py`.** A tool
importing a screen module looks like reaching into the UI; it isn't:
`cards.py` is the interface every stream is told to import, it never
touches the socket, and the server installs its broadcast. The
alternative — passing card builders in through `cli.py` — would have
hidden the dependency without removing it.

**2026-09-25 — A tap on the media Choice card plays without a second
`Registry.call`.** Raised in review as "a tool called without its
permission check". The check gates what the *engine* may execute: the
card only exists because a permission-checked `search` put it there,
and the tap is her answering the question that call asked, not a new
intent from the model. Routing a tap back through the registry would
need the browser to hold a permission grant, which is the wrong
direction for trust. Kept as is; if `"music"` is ever withdrawn at
runtime the search that would offer a card is what's refused.

**2026-09-25 — One search result is offered as a Confirm card ("Play
X?"), not a Choice of one.** `choice()` refuses one option, correctly;
review found the card path raising on it. A yes/no is the honest shape
of the question.

**2026-09-25 — A video the player reported unplayable is filtered out
of every later offer and the rest renumbered.** Review found a tap on
such a result clearing the card and saying nothing. Not offering it is
simpler and kinder than a second card explaining why it didn't play.

**2026-09-25 — A release is routed the way its press was, not by
`hold.active` at release time.** Review found two mirror-image leaks
when a hold handler was set or cleared while the key was down: core
stranded in LISTENING with the capture running, or a release with no
press behind it. The hold timer also exits on `abandon()`, so
`hold.clear()` mid-press no longer leaves a task ticking forever.

**2026-09-25 — `readback()` only regroups phone-number shapes;
decimals and times pass through.** Review: "37.5" was becoming "375".
A "." or ":" now means "not a phone number".

**2026-09-25 — Calling is in scope, reversing 2026-09-18's "calls/music
out for v1".** Explicit brief (Stream B). The stub was built so "v2
swaps an implementation rather than inventing plumbing" (SPEC.md); that
is exactly what `tools/calling.py` does — same name, schema and
`"calls"` permission, only the handler changes. Granting `"calls"` in
`cli.py` is a shared-file edit, proposed as a diff in
`docs/completed/calling.md`, not applied. SPEC.md's "Out: calls" line
is proposed for amendment the same way.

**2026-09-25 — Twilio Media Streams, not SIP or LiveKit.** Brief-given,
recorded for the trail: Media Streams is a bidirectional WebSocket of
20 ms μ-law frames — the same shape as the cascade's audio path — with
no registrar, NAT traversal, media stack or second always-on vendor.

**2026-09-25 — μ-law and 16k<->8k resampling are ~60 lines of numpy in
`call/codec.py`, not `audioop` and not a new dependency.** `audioop`
works on the 3.12 this runs today and is *removed* in 3.13, which
`pyproject.toml`'s `>=3.12` permits; a resampling library is a
dependency for one ratio. The encoder is bit-exact with the G.711
reference (`audioop` is used as an oracle where it still exists, all
65536 inputs); the resampler is a pair-average decimator and linear
interpolation, adequate for a 3.4 kHz telephone band.

**2026-09-25 — The relay is a cloudflared quick tunnel behind a
three-method `Relay` protocol; production is a real always-on service.**
Twilio connects inwards and the device is behind home wifi. Quick
tunnels need no login and cost nothing; they also change hostname per
start and take 60-90 s to become resolvable (measured here: up in 7 s,
publicly reachable at 84 s) — which is why `CallController.prepare()`
brings the relay up at boot, not on the first dial. ngrok (new vendor,
interstitial page), an SSH reverse tunnel (needs a VPS + key on the
device) and a *named* cloudflared tunnel (needs a Cloudflare login on
this box; the obvious production shape) lost for now. cloudflared
2026.9.3, Apache-2.0, sha256 verified against GitHub's asset digest,
installed to `~/.local/bin`, never vendored.

**2026-09-25 — The media server runs on its own thread and event loop
(port 8768), not on the screen server's loop.** The face has a 100 ms
reaction budget and is another stream's territory; a phone call's
socket sharing that loop would put every 20 ms frame in the face's way.
`build_media_app()` stays a plain aiohttp app so a fake Twilio peer
drives it in tests.

**2026-09-25 — Streaming playback for the far end is `pacat --playback`
fed on stdin, in `call/audio.py`, not in `audio/playback.py`.** There
is no streaming playback anywhere in this codebase and `playback.py`
is `paplay` on a file. The right home is a general `play_stream()` in
`audio/playback.py`; that file is not this stream's to edit, so the
writer lives beside its only caller and the move is proposed in the
completion doc. `--latency-msec=60` bounds Pulse's own buffering so
hang-up doesn't leave a tail of far-end audio playing.

**2026-09-25 — No new `core.py` state for calls; `HANDOFF` is not
re-purposed.** SPEC.md defines `HANDOFF` as "a question goes to the
slower, smarter path", resolving into the same turn. A real `IN_CALL`
state is a `core.py` + `Face` change (one of the five interfaces) and
is proposed, not built. Until then `CallController.active` is the "a
call is happening" signal initiative's gate should read.

**2026-09-25 — Hang-up is a two-second spacebar hold, registered
through a `HoldSeam` protocol that SCREEN implements; a single tap
during a call does nothing.** Double-tap lost: hard timing for older
hands, easy to trigger by accident. The seam is stubbed
(`FakeHoldSeam`) until SCREEN's real hold handling lands; calling never
touches the spacebar or renders a card itself.

**2026-09-25 — Twilio auth is an API key + secret, not the account auth
token, and every Twilio error is re-raised `from None` as a
`TwilioError` carrying only an operation name and HTTP status.** A
leaked key is revocable without rotating the account; `urllib`'s
`HTTPError` text carries the URL (account SID) and a request log would
carry both phone numbers, so nothing from the original exception
survives. Consequence: `X-Twilio-Signature` validation on `/twiml`
needs the auth token the device doesn't hold — that check belongs to
the production relay, and until then the tunnel's random hostname is
the only guard. Recorded as debt.

**2026-09-25 — Stage 1's `call_contact` dials only "the test number"
(any contact matching /\btest\b/i) and answers everything else with a
polite "not yet" note.** Brief-given ordering: contacts are not built
on an audio path that hasn't been heard working. The note is how the
model is steered to say "Calling the test number." — a tool never
speaks through `VoiceSession`.

**2026-09-25 — Contacts are `entities` + `edges`, not a new table; the
convention is recorded in `saathi/call/contacts.py`.** Brief-given, and
the reason is the product's: the daughter she talks about and the one
she phones must be one person. `kind="self"` (name `"self"`, lowest id
wins) is the src of every relationship; `kind="person"` carries the
phone as JSON in `notes` (`{"phone","country"}` — the only free field);
`edges(self, person, relation, since, until=None)`. Nothing that builds
the model's context reads `entities`, so numbers never reach the model.

**2026-09-25 — A wrong number is superseded, not corrected: latest
wins.** `IdentityStore` is append-only on `main` and `edges` has no id.
A new number is a new person row with the same (normalised) name; reads
take the highest id per name and the most recent open edge per
relation, and a relation resolves to a person *through the name* so an
old edge still reaches the new number. History stays. Nothing depends
on the proposed `retire()`; when it lands, superseded edges can get
`until`.

**2026-09-25 — Country for a number without "plus": stored `country`
preference, then the device timezone, then language only where it
names one country (hindi -> IN).** Timezone beat language: a Mandarin
speaker in Singapore is the case this product exists for, and English,
Chinese and Bengali each span several countries. The inferred code is
always *said* on the read-back ("That's a Singapore number, plus six
five."), and a confirmed save writes the `country` preference so it's
learned, not configured. No locale at all -> she is asked which
country. A small country table replaced `phonenumbers` (large new
dependency for nine countries); any other country works by saying
"plus".

**2026-09-25 — The model passes the number exactly as she said it; the
parser does the digits.** Asking the model to normalise lost: a model
"correcting" a digit is the silent wrong digit the read-back exists to
prevent. Homophones ("for", "to", "won", "ate") are *not* digits — "the
number for Priya" would gain a 4 — they are reported as unknown words,
which lowers confidence and is said back.

**2026-09-25 — Across turns, a number fragment is appended only while
the draft is too short.** Otherwise new digits replace it (a different
number) and a restatement from the start replaces it. Found by a test:
the first version appended a full second number to a complete first
one. After a "no" on the read-back, the name and relation are kept and
the whole number is asked for again — re-saying beats naming which
digit was wrong. Drafts expire after 10 minutes.

**2026-09-25 — A save needs a name *or* a relation, not both.** "Save
my daughter's number" is complete: the relation is stored as the name
until she gives one. Asking for a name she didn't volunteer would break
"ask only for what's actually missing".

**2026-09-25 — `save_contact` needs a new `"contacts"` permission;
`answer_card` uses `"calls"`.** Saving writes her memory with no
external consequence; placing a call has one. `answer_card` can finish
a save or (Stage 3) choose who to ring, so it takes the stronger scope.
Both are granted in `cli.py` (a proposed diff, not applied).

~~**2026-09-25 — Cards are a local stub shaped exactly like PR #3's
`saathi/screen/cards.py`, not an import from `batch/youtube`.** Same
builders, same `show`/`clear`/`answer`/`on_answer`, same `Answer`
fields; the swap at merge is one import line. Calling never calls
`ask()` — it would hold THINKING with the mic closed inside a tool
handler. Assumed, to confirm against PR #3: `answer()` runs `on_answer`
callbacks synchronously, and `{"choice": n}` is zero-based.
`answer_card` is calling's own voice-answer tool; if SCREEN ships one,
it wins at merge.~~


**2026-09-25 — Correction: card choices are 1-based, not 0-based.**
Supersedes the stub-assumption entry above. Checked against PR #3's
`saathi/screen/cards.py`: `validate_answer` accepts only
`1 <= n <= len(card.options)`, options are numbered 1..3 on screen and
in speech, and `Answer.choice` carries that same number. My stub
assumed 0-based and `answer_card` subtracted one — "the first one"
would have been sent as `{"choice": 0}`, which the real controller
rejects. Now the stub validates exactly as PR #3 does (`{"choice": 0}`
and one past the end are refused, nothing happens), `answer_card`
passes her number through unchanged, and a regression test pins it.
The other assumption held: `answer()` runs `on_answer` callbacks
synchronously on the answering thread, after its lock is released.
Cards remain a local stub of PR #3's shape; calling never calls `ask()`;
if SCREEN ships its own voice-answer tool it replaces `answer_card`.

**2026-09-25 — Name matching: sound folding + Jaro-Winkler, three bands,
thresholds from a 41-pair corpus.** `call/match.py`, pure Python. Folds
ph/dh/th/bh/gh/kh, sh/zh/ch, q->c, x->s, k->c, ee->i, oo->u, v->b, w->b,
y->i, final ng->n, doubled letters; strips accents and pinyin tone
digits; scores both orders of a two-word name and, for a one-word
request, each word of a saved name ("Priya" -> "Priya Sharma"). Corpus
(`tests/test_call_match.py`): 24 same-person pairs score >= 0.956, 6
ask-her pairs 0.800-0.925, 11 different-people pairs <= 0.783.
**Confident >= 0.93 *and* >= 0.05 ahead of the runner-up; unsure >=
0.79; below that, none.** Both gaps are thin (0.925 vs 0.956; 0.783 vs
0.800) and 0.79 was moved down from 0.80 when Wong/Wang landed on the
line at 0.7999 — the threshold sits at the midpoint of the gap, not on a
corpus point. Expect to retune from real misses; the corpus is where to
add them. Soundex/Metaphone lost (English-centric, no pinyin), edit
distance lost (names agree at the start; JW's prefix bonus rewards it),
jellyfish/rapidfuzz lost (a dependency for ~40 lines).

**2026-09-25 — Zhou/Chou stays a non-match (0.778).** Chou is Wade-Giles
for Zhou, so they can be one surname — but the brief's table folds zh->z
and ch->c separately, and folding zh with ch would also merge Zhang and
Chang (different surnames). Recorded rather than special-cased; a saved
"Chou" she calls "Zhou" gets "no number for Zhou" and an offer to save,
never a wrong dial.

**2026-09-25 — Exact names go through the matcher, not around it.** The
brief put fuzzy matching after an exact-name lookup. An exact lookup
first would dial "Meena" outright even with "Mina" also saved — letting
Whisper's spelling of the day pick who rings, which is the failure the
bands exist to stop. Through the matcher an exact name scores 1.0 and is
confident unless a sound-alike is saved, in which case she is asked.
The relationship lookup ("my daughter") still runs first: an edge is
something she told us, not a spelling.

**2026-09-25 — Unsure with one plausible name is a Confirm card ("Did
you mean Anand?"), not a Choice card.** A Choice card needs two options
(PR #3); a single sub-confident name still must not dial on its own.

**2026-09-25 — A choice answered by voice dials inside the `answer_card`
call; a choice answered by tap dials on a short worker thread, and
rings without "Calling X." being said.** The voice path can report
"Calling Basudeb." (or a failure) in the same turn. A tap arrives on the
screen server's loop, which must not block on a Twilio request, and
nothing may speak outside a turn without reaching through
`VoiceSession`; the name on the card she just tapped stands in.

**2026-09-25 — Hang-up tears down at once and tells Twilio on a worker
thread.** Found reviewing the wiring, not in a test: PR #3's
`HoldController` fires its handler on the screen server's event loop,
and `complete_call` is a blocking HTTP request with a 15 s timeout — a
slow Twilio would have frozen the face. The mic and sink are released
and the state goes IDLE synchronously; only the REST call moves off the
thread. `hangup(wait=True)` joins it for `shutdown()` and scripts.

**2026-09-26 — A tap that answers a card ends the exchange; the turn
that asked is over.** Reproduced in a real headless Chromium against
`screen/server.py` (not by reading the code): the search turn shows the
card while THINKING and then reads the three titles for the rest of the
reply; a tap on option two cleared the card and started the video, and
the reading carried on. `server.py` now treats an accepted tap on a card
shown by the live turn as that turn's answer: `session.interrupt()`
while SPEAKING (the turn's own tail fires `done`), or supersede plus
`no_response` while THINKING. A card shown by an earlier turn or between
turns ends nothing. The alternative — leave the turn alone and let the
reply finish — is exactly the symptom. Only `core.py` moves the state;
the server asks it for the one event that fits.

**2026-09-26 — A tool result may carry `say`: the exact words, or `""`
for none.** `cascade.py` then skips the second model call. `play`
returns `say: ""` so "the second one" starts the song and nothing is
said over its opening; the exchange is still recorded (the result's
`did`) so the next turn knows what is playing. Rejected: a stronger
note asking the model for "one short sentence" — that was the note
already there, and she kept talking. `search` keeps the model in the
loop (it can phrase the framing in her language); only the pick is
silent.

**2026-09-26 — Each sentence is spoken in the voice of its own script.**
`voice/language.py`'s `script_language` decides per sentence (Han →
Chinese, Devanagari → Hindi, Bengali → Bengali, Latin → English, no
letters → the turn's language); `cascade._speak` groups consecutive
sentences by language and chains one synthesis stream per group, so an
ordinary single-language reply is still one call. `split_into_sentences`
now also splits on 。！？. Consequence: a Latin-script sentence in a
Hindi or Bengali turn is read by the English voice, which is wrong for
romanised Hindi and right for an English title; the title case is the
one that was observed, the other is not, and the model is asked to reply
in the language's own script anyway.

**2026-09-26 — A search is two API calls: ten candidates, then
`videos.list` for `status.embeddable`.** `videoEmbeddable=true` on
`search.list` is a hint the API does not honour reliably. Private,
unprocessed and age-restricted videos are dropped too (an age-restricted
embed asks for a sign-in the kiosk can't give). If the status call fails
the unchecked results are offered and the log says so, rather than no
results at all. One extra quota unit per search.

**2026-09-26 — A video the player could not play is re-offered without
it, on the card, not swallowed.** The browser reports the error with the
player's code; the server logs it; the controller puts the remaining
results back on the card ("That one won't play here. Which instead?"),
shown between turns so nothing speaks it — she sees the device noticed,
and the model learns from the next tool call. The panel's own silent
failures are gone too: the API script and the wrapper each have a
deadline after which the play is reported as an error (codes `api`,
`no_ready`) and the next play starts fresh, instead of every later play
queueing behind a wrapper that never calls back.

**2026-09-26 — The card that is up is re-sent on connect.** The server
holds the question; the browser only draws it; a reload or a kiosk
restart must not leave a pending question with nothing on screen to
answer. Reverses "never on connect" (2026-09-25), which was symmetry
with media messages, where a message about a screen that isn't there
really has nothing to say.

**2026-09-26 — YouTube titles are cleaned hard, and capped by display
width.** Bracketed runs, symbols and emoji, "(Official Video)"-style
boilerplate in English and Chinese, track listings ("01 …；02 …") and
repeated segments go; a CJK character counts double toward the cap, so
a Chinese title is about as long to say as an English one. 《》 marks
are dropped but their words kept: they wrap the song's own name.

**2026-09-26 — Chirp is the default preference; Piper stays the
fallback.** `DEFAULT_PREFERRED_BACKEND_ID` (what a database with no
`tts_backend` row means, in `cli.py` and the panel) is separate from
`DEFAULT_BACKEND_ID` (what `cascade.py` uses when the preferred backend
is unavailable). The user's call, 2026-09-26. `saathi voice
google-chirp3-hd` (or `saathi voice chirp`) switches an existing
database; `saathi voice` shows the effective value.

**2026-09-26 — `cli.py` is split into `build_runtime()` and `_run()` so
the wiring is tested.** Every tool registered, every permission granted,
one set of controllers shared by the tools and the screen — asserted in
`tests/test_cli.py` with the outside world faked at the seams cli.py
imports it from.

**2026-09-26 — The `[hidden]` attribute wins over every `display:` rule
in `style.css`.** Found in the real stylesheet in a headless Chromium:
`.media-results { display: flex }` beat the browser's own
`[hidden] { display: none }`, so the results list and the player were
both drawn at once in the no-cards path.
