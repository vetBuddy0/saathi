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

**2026-09-26 — Clear commands are routed to their tool by a rule, before
the model; no second model.** Live, "pause", "volume up", "smaller" and
"call my son" sometimes came back as a sentence ("I've turned up the
volume for you") with no tool call: gpt-4.1 with six tools offered
described the action instead of asking for it, one turn in several.
`voice/router.py` now matches the transcript first -- pause / carry on /
stop / louder / quieter / bigger / smaller / next / again / never mind /
"the second one" (while titles are on offer), "call my son" / "ring
Priya" (always), yes / no / a number while a calling card is up -- and
`cascade.py` emits the match as an intent through the same callback and
permission check a model's tool call goes through. The model decides
nothing for those turns; fuzzy requests reach it unchanged. Fixed
replies ("Paused.", "Carrying on.", "Stopped."; silence for bigger /
smaller / never mind) apply only when the tool answers `status: "ok"`,
and volume is always phrased by the model from the note ("already as
loud as it goes" comes back under the same status). Media commands only
match once a search has returned titles this session: "louder" with
nothing playing means her own voice. "Hang up" is not routed -- calling
has no tool for it; the two-second hold is the hang-up. Multi-clause
utterances match on the last clause only, or not at all. The router's
context is inferred from tool results as they pass through the session
(titles on offer; a card shown, for one turn), because `cli.py` is not
touched here; wiring the controllers' real state in is a later, small
change. Kill switch: `SAATHI_COMMAND_ROUTER=off`. A routed turn logs
`first_token_ms=0` and no prompt tokens when no model was called.
Rejected: a second, smaller model choosing the tool (a two-model split,
TypeSafe's JEV) -- still probabilistic on exactly the inputs that fail,
one more network hop on every turn, and JEV needs an OpenRouter key this
project doesn't have; it stays a next step only if fuzzy tool choice
turns out to be the remaining failure (TODO.md).

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

**2026-09-26 — An unanswered call ends by itself: a ring watcher polls
`fetch_call`, not a StatusCallback.** S1 fix. While DIALLING, a daemon
thread polls Twilio every 2 s and tears the call down on a terminal
status or after 45 s of ringing (then completes it via REST so the far
end stops ringing). A StatusCallback would need another public route on
the relay and would never arrive if the tunnel is what died -- the
timeout covers that case too. The watcher stops the moment the stream
opens; an answered call is still ended by its stream.

**2026-09-26 — Calling is wired into `saathi run` on `cloud/demo`; the
relay comes up at boot on its own thread, and "unavailable" is a tool
answer, never a crash.** `cli.py`'s `_build_calling` follows the diff in
`docs/completed/calling.md`: `call_contact`, `save_contact` and
`answer_card` registered, "calls" and "contacts" granted, the
echo-cancelled source/sink from `aec.py` threaded into the call audio,
the screen's `HoldController` as the hang-up seam. The quick tunnel's
hostname took ~84 s to resolve on the first live run, so `prepare()`
(media server + cloudflared) runs on a daemon thread at boot with a
150 s budget; until it sets `ready`, `call_contact` answers "still
starting up". Missing Twilio variables, no cloudflared, no echo-cancel
pair, or a tunnel that never resolves all leave a `call_contact` that
answers "unavailable" with the reason, and the rest of the device runs
as before. Rejected: starting the tunnel at the first dial (a minute of
silence after "call Priya") and blocking boot on it (the face waits).
The two audio diffs in that doc (`play_stream`, making the echo-cancel
pair Pulse's default) are not applied here.

**2026-09-25 — Speech-to-text and replies move to OpenAI for the demo
(user's decision, "change later if required").** A live session on Groq
failed two ways: its on-demand tier caps output at 1000 tokens/minute
and rejected tool turns outright, and the model sometimes answered
"help me play a song by Ed Sheeran" with "Of course, which song?"
instead of calling the music tool (1 in 6 with all six tools offered).
OpenAI is a new vendor and new spend; the user chose it explicitly.
Both services share the `audio.transcriptions` / `chat.completions`
shapes, so the switch is one seam (`voice/engine/provider.py`), not a
second engine; Groq stays selectable (`SAATHI_AI_PROVIDER=groq`).

**2026-09-25 — `gpt-4.1` for replies, chosen by measurement, not by
name.** The live failure case (six tools, a prior exchange) four times
per model, `max_completion_tokens=400`: gpt-4.1 4/4 at 742 ms median;
gpt-5.4-nano 4/4 at 786; gpt-5.6-luna 4/4 at 1029; gpt-5.6-terra 1131;
gpt-6-sol 1284; gpt-5.4-mini 1272; gpt-6-luna 2677; gpt-4.1-mini 0/4
(the first guess -- it would have repeated the failure); gpt-6-astra
refuses tools without reasoning. The fastest model that never missed
wins: the model call sits directly in the pause before she answers.
`SAATHI_LLM_MODEL` switches it.

**2026-09-25 — Reasoning off for OpenAI's reasoning families.** gpt-5.x
and gpt-6 reject function tools on chat completions unless
`reasoning_effort="none"` (the API's own message). Sent automatically
for those model names only; reasoning is latency a voice turn can't
spare. The option that lost, the Responses API, would be a second call
shape for one provider.

**2026-09-25 — `gpt-transcribe` for speech-to-text; language from the
transcript's script.** gpt-transcribe, gpt-4o-transcribe and
gpt-4o-mini-transcribe were all exact on English and Mandarin test
sentences (~750-870 ms); whisper-1 took 1.7 s. The newest was chosen,
knowing clean synthetic speech can't show which copes best with a
noisy mic. These models return no detected language, so the cascade
reads it from the transcript's script (`script_language`, already
used for YouTube titles): Han -> chinese, and so on.

**2026-09-25 — Every chat call reserves at most 400 output tokens
(`max_completion_tokens`).** The unbounded default reserved 2048
against Groq's 1000/minute and got tool turns refused. Replies are
capped at two sentences; 400 is ample. `max_completion_tokens` because
OpenAI's newer models reject `max_tokens`.

**2026-09-25 — Captions, off by default, from the Ctrl+L panel.** A
strip along the bottom showing what speech-to-text heard and what she
said, for whoever is demoing or setting up the device -- the first live
demo failed in ways nobody could see (was it the mic, or the model?).
Captions are content, not status: the words said, never "Listening...",
so CLAUDE.md's rule against status text under the face is not what's
at stake; nothing is sent while they're off. The option that lost was
terminal logging only: the person demoing looks at the screen.

**2026-09-25 — Demo-only: YouTube played from a direct stream (yt-dlp),
reversing "official APIs only" at the user's explicit instruction.**
"Ed Sheeran - Perfect" (and similar label uploads) report `embeddable`
and `syndicated` through the official API and still fail in the embed
with error 150; no API field predicts it, so no filter can. The user
chose the unofficial route knowing it breaches YouTube's terms -- the
reason the original brief ruled it out, and still the reason it must
not ship. Scope kept small: search stays on the Data API; only playback
changes; opt-in via `SAATHI_YOUTUBE_PLAYBACK=direct`, default remains
the official embed. YouTube no longer serves any combined audio+video
file (checked with a JS runtime too), so the page plays a muted
<video> and an <audio> in step, audio as the clock. New dependencies:
yt-dlp (Unlicense), deno (MIT, the JS runtime yt-dlp needs).

**2026-10-05 — Contact names are matched across scripts; the no-match
card ranks by sound.** Live: "call Udhi" in a Hindi turn came back from
the transcriber in Devanagari, scored 0 against the Latin "Udhi", and
fell through to a card of the first three saved contacts -- so the
fourth could never be picked. `call/match.py` now transliterates
Devanagari and Bengali (one ISCII-shaped table for both blocks, schwa
dropped word-finally) before folding; the fallback card sorts saved
contacts by similarity, saved order on ties. No threshold changed; the
corpus gained six cross-script pairs. Lost: `unidecode` (a dependency
for one table) and telling the model to pass names in Latin (a prompt
can't guarantee it). `call_contact` now logs the name it was asked for
-- her words, never a number -- so a mis-dial can be debugged.

**2026-10-08 — The yt-dlp direct stream is gone; every `play` names a
target, the embed first and the real watch page only after the embed
refuses.** The 2026-09-25 detour breached YouTube's terms and was
never to ship; it is removed whole (`direct_playback_enabled`,
`resolve_stream_url`, the muted `<video>`+`<audio>` player, yt-dlp,
yt-dlp-ejs and deno from the lock). What replaces it: the controller
sends `target: "embed"` (and `watch_url`) with every play; when the
embedded player reports an error -- any code of its own: 150/101
embedding disabled, 100, the panel's "no_ready"/"api" deadlines -- the
video goes into `unplayable` and from then on is sent with `target:
"browser"`, which `media-panel.js` ignores and a client that can show
youtube.com itself (the Android shell's YouTube pane, on its own `/ws`)
plays. The embed is the default because it is the one route YouTube's
terms plainly allow; the watch page under automated control is a grey
area in those terms -- a contract question, not a copyright one -- and
so is only ever the fallback, never tried first. Three Google
identities keep that boundary honest and auditable: the Data API
project that searches, the account signed into the kiosk's WebView
that watches, and the developer's own account, which does neither.
Only the browser's own verdict (a `media_event` error with code
"browser" or "wall") refuses a video for the session (`refused`), drops
it from searches and puts the rest back on a card; the card's title now
names the video, since by then the player is gone and "that one" would
point at nothing. Lost: dropping embed-refused videos from the offer
(the old rule). The most popular result for a search is usually the
label upload the embed refuses, and losing it every time was the
complaint that produced the yt-dlp detour.

**2026-10-08 — An embed refusal of the video she is waiting on falls
back to the browser target by itself; a play that changes target is
preceded by `stop`.** The alternative -- mark the video and wait for
"again" -- shows her a blank panel first and asks her to know why. The
re-emission happens in `on_browser_event`, from the controller, so
"again"/"next"/"carry on" take the same decision through the same
`_play`. A repeated embed refusal for a video already on the browser
target (a second face page open, or a client reporting under the wrong
code) is logged and re-emits nothing, so nothing can loop by itself.
The `stop` before a target switch is the controller's, not the clients':
without it the embed and the watch page would play over each other,
and a panel that reacted to the other target's plays would be two
clients each half-deciding the same thing. On a laptop with only the
face page a browser-target play has no taker and nothing plays
(docs/DEMO.md says so); the controller cannot tell who is connected,
and inventing a hello on `/ws` for it belongs with the shell's step.

**2026-10-08 — Renumbering keeps the thumbnail.** `youtube_search`,
the search's refused-filter and the re-offer all rebuilt each
`MediaResult` from id and title, so the picture the card was given on
2026-09-25 never reached it from a real search. `dataclasses.replace`
now; one new test pins it.

**2026-10-08 — The phone is the microphone and the speaker; the engine
stays on the Pi or the laptop. One WAV per sentence over `/audio`, not
a PCM stream.** `audio/remote.py`'s `RemoteAudio` is the engine's side
of the Android shell: a second WebSocket path, one client at a time,
binary PCM16/16 kHz frames in (forwarded to the session only between
a press and its release on `/ws`, whatever the client sends), and for
each TTS sentence a `play` header, one binary WAV and a `played` ack
back; `stop` cuts it short. `CascadeSession` gained a `player`
constructor seam with `play()`'s signature; `cli.py` hands it
`RemoteAudio.player`, which sends to the attached client and falls
back to the local `paplay` when none is -- the session never knows
which. With no client the server is byte-for-byte what it was. Lost:
streaming the reply as PCM chunks the way the mic comes in. The
cascade already synthesises and plays one whole sentence at a time
(`synthesize_stream`), so chunking would have bought no
time-to-first-audio and cost a jitter buffer on the phone and a second
barge-in clock on the engine; a client is a dumb speaker -- play this
file to the end, then say so. Also lost: the engine in the APK now.
The seam is shaped so it can move later without the shell changing.
Decided alone, and recorded here, four smaller things. A play waits
for its ack at most the WAV's length plus 3 s, then moves on with a
warning -- a dead phone costs one sentence, never a face stuck on
"speaking"; the client leaving mid-sentence releases the wait at once.
Every send to the client joins one ordered chain on the loop, so a
`stop` cannot overtake the frames of the play it stops. A press with a
client attached needs no local capture source to run a turn -- the
phone *is* the mic -- though `cli.py` still only builds a session when
the local echo-cancelled pair exists, so this matters to tests today
and to a mic-less laptop later. And the route is registered only when
`build_app` is given a `remote_audio` (always, from `cli.py`), so
every checkpoint-1 test and the fake press/release path see no
difference at all.

**2026-10-08 — The Android shell is its own Gradle build under
`android/`, not a module of the Python package, and it is not compiled
in CI yet.** The repo's CI runs the Python suite; `uv run pytest` must
never need a JDK, and this container had no Android SDK (dl.google.com
is unreachable), so nothing under `android/` has been through AGP. What
was verified instead: the pure-Kotlin files (`Protocol.kt`, the three
interfaces, `EngineAddress.kt`) and their JUnit tests were compiled with
Kotlin 2.0.21 and run on a plain JVM in a scratch project, with warnings
as errors. The Android-dependent files (`MainActivity`, `Settings`,
`SetupDialog`, the receivers, the service) were written against API
26-34 by hand and have not met a compiler; the README says so, in
those words. Lost: skipping the Kotlin check because "it can't build
anyway" -- the protocol parser is the thing most likely to be wrong,
and it is the thing that *can* be checked.

**2026-10-08 — Cleartext "only for private LAN ranges" is enforced in
code, not in the network security config.** Android's config matches
host names (with an optional subdomain suffix); it has no CIDR, so
`192.168.0.0/16` cannot be written there, and listing one household's IP
would make every household a rebuild. The config therefore permits
cleartext in its base config, and the rule lives at the one gate an
engine address passes through: `EngineAddress.normalise()` refuses to
store anything that is not RFC 1918, link-local, loopback, ULA or a
`.local` name, and the shell loads no cleartext URL it did not get from
there (the YouTube pane is https). Lost: a false base config with a
hand-kept domain list. Raised rather than narrowed silently: the brief
asked for the config to do it, and the config cannot.

**2026-10-08 — `EngineAddress.DEFAULT` is a placeholder address, and
that is not the device name CLAUDE.md forbids.** A card number in config
is a detected thing written down and wrong after a hotplug; the default
URL is the pre-filled text of a dialog whose purpose is to replace it,
and nothing is detected from it or depends on it. Recorded because the
rule will look violated to the next reader. mDNS discovery should
pre-fill that dialog eventually; it lost for now because a discovery
that fails silently on a Wi-Fi that drops multicast is worse than a
dialog, and `Settings.kt` says so.

**2026-10-08 — The setup dialog (five-second hold on the face) is in
step 1, not step 3.** Without it the engine address could not be set on
a real phone and the README's "run on a phone" would be false. A
long-click listener lost (fires at ~500 ms, which a resting thumb trips)
and a hidden tap sequence lost (what a curious grandchild finds first).
The hold is a touch listener that returns false, so the WebView still
gets every event; text selection and haptics are turned off on the face
WebView so the only long press it knows is this one.

**2026-10-08 — The pure half of the shell is split from the Android
half so it can be tested without a device.** `EngineAddress.kt` (no
Android imports) beside `Settings.kt` (SharedPreferences), and
`Protocol.kt` on `org.json` with the real `org.json:json` on the test
classpath, because the platform's copy is a stub in local unit tests
and every method throws "not mocked". Lost: kotlinx.serialization or
Moshi for the protocol -- a code generator or a reflection runtime for
seven small shapes, and both stricter than an engine that adds fields
between releases. An unknown `type` parses to `UnknownMessage`, never a
crash; a frame with no string `type`, or a known type missing the one
field that gives it meaning, parses to null and is dropped.

**2026-10-08 — The talk button is a child of the root, not of the face
pane.** A fullscreen video (step 3) takes the face pane down to a
corner; the button must not go with it. Same reason the layout's root
is a FrameLayout around the horizontal LinearLayout the brief asked
for, rather than the LinearLayout itself.

**2026-10-08 — `BootReceiver`, `SaathiAdminReceiver` and `EngineService`
exist as classes, not only as manifest lines.** A manifest that names a
class the APK does not contain installs fine and crashes at the first
broadcast; the three are a few lines each, with their reasons in their
headers. `EngineService` does nothing yet: on API 34 its `microphone`
type must be in the manifest at install time, so the declaration is
step 1's and the behaviour is a later step's.

**2026-10-08 — `OkHttpEngineLink` keeps all of its state on the main
thread; OkHttp's threads only post to it.** One socket reference, one
connected flag and one attempt counter, mutated only by `Handler`
callbacks on the main looper, which also times the backoff. Lost: a
lock around the three (every OkHttp callback would take it, and the
listener would still have to hop to the main thread to touch a view),
and coroutines (channels and a retry loop for what is twenty lines of
state). The cost accepted: a window of a few milliseconds between the
socket opening on OkHttp's thread and the main thread hearing of it,
during which a send is dropped rather than queued. Also lost: a
WebSocket of our own over `java.net.Socket` -- framing, masking,
ping/pong and the close handshake are a few hundred lines OkHttp
already has, and `build.gradle.kts` names OkHttp as the one
dependency with a job the platform cannot do.

**2026-10-08 — A frame sent while the engine link is down is dropped
and logged, never queued.** `main.js` sends only on `readyState ===
OPEN`; the link follows it, although OkHttp would happily enqueue a
message during a handshake and deliver it on open. A press that lands
seconds late is a turn she did not start (the engine would open the mic
on a room with nobody holding the button), and a release that lands
late ends one she did. The listener hears `onDisconnected` only when a
connection that was up goes down, and `onConnected` on every open;
failed attempts in between are logged, not reported -- the link's job
is to be up, and there is no status to show for it being down.

**2026-10-08 — The reconnect schedule and the delivery rule are pure
and tested on the JVM; the socket is not.** `ReconnectBackoff` pins
`min(30000, 500 * 2 ** attempt)`, reset on open, to the face page's
constants so a restarted engine sees both clients come back in step;
the shift is capped so a night of retries cannot overflow.
`OkHttpEngineLink.deliver()` is the whole of what a `/ws` frame may do
to a listener (`state` and `media` reach it, cards, captions, settings
and unknown types stop at the link). Testing the socket itself would
need Robolectric for `Handler` and a fake server for OkHttp, neither of
which a build without the SDK can run; same split as `EngineAddress`
from `Settings`.

**2026-10-08 — The link needs OkHttp pings, and `newClient()` is the
client that has them.** A Wi-Fi hop that dies silently leaves a socket
"open" with every press going into it unanswered, and nothing in the
`/ws` protocol would ever notice; a missed pong fails the socket, which
is what starts the reconnect. Lost: an application-level heartbeat
frame on `/ws` -- the server has no such frame, and adding one changes
a protocol the face page also speaks for a problem the transport
already solves. The interval (15 s) is the time a dead hop can go
unnoticed; shorter costs a sleeping tablet its battery for nothing.

**2026-10-08 — `PushToTalk` consumes every touch on the talk button
and sets the pressed state itself.** A `Button` turns down-and-up into
a click with a click sound on every release, and a click is not a hold;
the listener returns true, so the button's own touch handling never
runs, and drives `isPressed` so the drawable still shows the hold.
`performClick()` is deliberately not called (lint's
`ClickableViewAccessibility` is suppressed with that reason in the
file). Lost: `OnLongClickListener` (its ~500 ms clock is the engine's
hold seam's to keep), and a toggle (a state she would have to remember
and a label to show it, which the face must not have). `ACTION_CANCEL`
is a release: a press with no release would leave the engine listening
to an empty room. No timer of any kind: a hold is a long press, and the
engine times it from the one press it receives. `cancel()` exists for
the activity's `onPause`, the one case where the touch stream may not
deliver the cancel itself. Release sends the `/ws` release before it
stops the mic: the engine gates frames on press/release on its own side
(`audio/remote.py`'s `feed`), so a frame captured after the release is
dropped there either way, and the order on the phone is the order the
brief gave.

**2026-10-08 — The WAV header reader is its own pure-Kotlin file,
`WavHeader.kt`, beside the two files the audio brief named.** Same
split as `EngineAddress.kt` from `Settings.kt`: the parser is the part
of the speaker most likely to be wrong (a `LIST` chunk, an odd-sized
chunk, a streaming writer's 0xFFFFFFFF size field, a backend at 24 kHz
instead of 16) and the part that can be checked on the JVM without an
SDK, so it has no Android import and eleven JUnit cases. Lost: assuming
44 bytes and the engine's rate, which every backend here would satisfy
today and one ffmpeg flag would break. 16-bit PCM in one or two
channels is all it accepts; that is all the engine writes, and anything
else is acknowledged unplayed (below) rather than guessed at.

**2026-10-08 — `played` goes back through `OkHttpAudioLink.sendPlayed`,
not through the `AudioLink` interface.** Step 1's interface has no
path for the acknowledgement and is not edited here (another agent
integrates against it). The method is on the concrete class with the
gap named in its header; the integrator calls it from the `Speaker`'s
`onDone`. Raised rather than patched into the interface: an interface
change is a conversation, and this one needs step 3 in the room.

**2026-10-08 — The speaker streams through `AudioTrack` on its own
thread and watches the playback head; `MODE_STATIC` lost.** Static
mode wants the whole clip in shared memory and a position marker (with
a Looper for its listener) to learn that it ended; a thread that writes
in `MODE_STREAM` and polls the head until it reaches the last frame
ends exactly where the data does, for a clip of any length, and `stop()`
is a pause and a flush from any thread. A play is declared done by the
clock at the WAV's length plus one second if the head never gets there,
so the ack always beats the engine's "length plus three seconds" wait: a
device that lies about its head position mis-times one ack, never
stalls a turn. A WAV the reader refuses is acknowledged at once, so the
engine moves on now rather than after its grace; the link passes every
`play` through regardless of its `format` field for the same reason --
one path for every unplayable sentence, in the speaker.

**2026-10-08 — Playback asks for `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK`
with `USAGE_ASSISTANT`/`CONTENT_TYPE_SPEECH`, keeps it 1.5 s past the
last sentence, and stops on a `LOSS`.** May-duck over plain transient
because the engine's own rule (20%, `Ducking`) already decided a video
is lowered under her voice, not paused, and the system-wide focus
should say the same thing to the watch page and to every other app.
The hold-over is so a four-sentence reply is one duck, not four
(request/abandon per sentence pumps the video's volume). A `LOSS` or
`LOSS_TRANSIENT` (a call) stops the sentence because she cannot hear
both; a denied focus request is logged and the sentence plays anyway,
because a companion that goes quiet when something else holds focus is
worse than one that talks over it. Lost: no focus at all.

**2026-10-08 — The microphone is `VOICE_COMMUNICATION`, opened per
hold on one parked thread, with a buffer four times the device
minimum and the short tail frame sent on release.** The source asks
the platform for its own echo cancellation and gain control for a
near-field voice -- what "local AEC is mandatory" means on a phone,
where PipeWire's reference path does not exist -- with
`AcousticEchoCanceler`/`NoiseSuppressor` attached where the device has
them. Capture runs only between `startSending` and `stopSending`
(the interface allows always-on): the engine would drop the frames,
the privacy indicator would be lit all day, and a care facility's
network admin would ask why. One long-lived thread parked between
holds, so a second hold never races the first's release of the
AudioRecord. The buffer is `4 x getMinBufferSize` at run time, no
number written down (a number would be a device name). The partial
last frame goes up on release because it is the end of her sentence
and the engine joins frames of any size. Lost: `AudioSource.MIC`,
a thread per hold, and dropping the tail.

**2026-10-08 — The audio link reconnects after 1 s doubling to 15 s,
reset on open, timed on the main looper, with no jitter.** One client
per engine on one Wi-Fi: there is no herd to spread out, and a
deterministic schedule is the one that can be tested (`backoffMs`).
OkHttp's WebSocket has no read timeout after the upgrade, so a dead
Wi-Fi hop is only noticed through the client's `pingInterval`; the
link's header asks the integrator to set one on the shared client.

**2026-10-08 — The audio component was type-checked against
hand-written stubs, not the SDK, and that is recorded as what it is.**
No Android SDK in this container (dl.google.com is blocked), so
`OkHttpAudioLink.kt`, `AudioTrackSpeaker.kt` and `WavHeader.kt` were
compiled with Kotlin 2.0.21 (warnings as errors) against Java stubs of
the API 26 shapes they call -- signatures as published, bodies empty --
plus the real okhttp 4.12.0/okio 3.6.0 jars and the step-1 pure files,
and `WavHeaderTest`/`OkHttpAudioLinkTest` ran there (14 passed). That
catches Kotlin mistakes and interop shape (synthetic properties, SAM
conversion, overload choice); it cannot catch a wrong constant value
or a behaviour the real platform has. The first `assembleDebug` is
still the first real build, as the README says.

**2026-10-08 — The YouTube pane is two files: the rules in `WatchPage.kt`
(pure Kotlin) and the WebView in `WebViewYouTubePane.kt`.** The same
split as `EngineAddress`/`Settings` and `ReconnectBackoff`/
`OkHttpEngineLink`: what is a wall, what is YouTube at all, which user
agent to wear and what the injected scripts say are pinned by
`WatchPageTest` on the JVM; the WebView that applies them needs a
device. The scripts' behaviour on a page (the poll, the grace, the
deadlines) was driven under node against a fake `<video>` with fake
timers -- which is what found the poll giving up at 28 s instead of 30
-- but node is on neither the Python nor the Android test path, so that
harness is not in the repo and the JUnit test pins the scripts' shape
only. Lost: a Chromium test in `tests/` like `test_media_policy.py`'s
-- the scripts are generated by Kotlin, and a copy in a JS file for
pytest to load would be a second source of truth. The pane was
type-checked against hand-written stubs of the API-26..34 shapes it
uses, with the documented nullability on each override, not against
an SDK (none here); the first `assembleDebug` is still the first real
compile.

**2026-10-08 — The watch page's user agent is desktop Chrome with the
platform's own Chrome version.** YouTube serves the full player only to
a desktop browser and serves it by version; a version frozen in the
source is an "update your browser" banner a year on, so only the
`Chrome/x.y.z.w` of `WebSettings.getDefaultUserAgent` is kept and the
rest of the string is the canonical Windows one (`wv`, `Mobile` and
`Android` gone). A platform UA with no Chrome version falls back to a
fixed string.

**2026-10-08 — `ended` and `error` from the `<video>` count only after
a two-second grace.** YouTube plays its ads in the same element as the
content, so an ad ending fires `ended` too; the element is checked two
seconds later and an `ended` counts only if it is still at its end
(`ended` or `paused`) rather than playing what came next. The same for
`error`: the player recovers from a format it cannot decode by loading
another, and the element's `error` clears when it does. Lost: reading
`#movie_player`'s `ad-showing` class -- certain, and the page's DOM
rather than the element's, which is the line the pane does not cross.
Also added: a start deadline. A `<video>` that exists but has not
played thirty seconds after it appeared is reported (`no_start`, as
code "browser"): an age gate and "video unavailable" are drawn inside
the player with no navigation, so the host rule never sees them, and
`Protocol.CODE_BROWSER` already names "never started". The deadline
stands down while the pane itself has paused the video.

**2026-10-08 — The pane leaves the watch page on its own `ended` and on
every failure, and the face comes back with it.** `YouTubePane.kt`
says the pane decides nothing after reporting, and it still does not
decide what plays next; but a watch page left up after its video ends
autoplays a next one nobody chose within seconds, and the face page's
panel hides itself at the same moments (`view = "none"` in
`media-panel.js`). So `ended`, a wall, a page that failed to load and
a player that failed all end in `stop()` -- `about:blank`, the
container gone, the face restored if the pane had hidden it -- and
then the listener hears. Fullscreen, per the brief, hides the face
container outright (`GONE`), which diverges from the face page's
"small in a corner" and from `activity_main.xml`'s comment; it is one
method (`applyLayout`) and the integrating step can make it a strip
instead. The face is hidden only while the pane is showing: a `layout
fullscreen` with nothing playing leaves the face alone, and the pane
restores only what it hid.

**2026-10-08 — The pane shows youtube.com and nothing else; walls are
three hosts and one path; sign-in is the one exception.** Main-frame
navigations to a non-YouTube host are blocked (a tapped ad, a channel's
website: the engine stopped nothing, so the pane stays where it was);
`intent:` and `vnd.youtube:` links go nowhere a kiosk can show and are
dropped. `accounts.google.com`, `consent.youtube.com` and
`consent.google.com` (one more than the brief: Google's own consent
host serves the same wall) and any path containing `/sorry/` are
reported as "wall", checked at `shouldOverrideUrlLoading`,
`onPageStarted` and `onPageFinished` so a server redirect is caught
wherever it surfaces. `open()` refuses a URL that is not https on a
YouTube host without loading it: the engine builds these, the pane
checks them, and a kiosk WebView with a desktop UA and persistent
cookies must not be a general browser. `signIn()` (on the class, not
the interface) is the one time a wall is the point: it loads Google's
sign-in and the pane hides itself when Google returns to youtube.com,
flushing cookies then and only then (`CookieManager.flush()` blocks
the UI thread, and the store persists on its own; a flush on every
stop would be jank for a sign-in that is weeks old). Google may refuse
a sign-in from a WebView; the pane plays signed out.

**2026-10-08 — No hardware layer on the watch WebView; the renderer
dying does not kill the shell.** Video needs the window's hardware
acceleration, which targetSdk 34 gives every window; `LAYER_TYPE_
HARDWARE` on the view would be a second full-size texture with
reports of black fullscreen video, and a software layer must never be
set. `onRenderProcessGone` returns true: the WebView is taken off the
screen, every later `open()` is refused with "browser" until the shell
restarts, and the current video is reported lost. The face WebView
shares the renderer, so its client (a plain `WebViewClient` in step 1)
still decides the shell's fate; step 3's to change. The page's own
fullscreen (`onShowCustomView`) fills the pane, not the screen, so the
face and the talk button stay. The bridge's two methods take nullable
strings: a page passing `undefined` must not throw inside the
WebView's thread. Every code the page reports (`no_video`,
`no_start`, a MediaError number) is logged and sent as "browser"; the
controller treats the two browser codes alike and only the log needs
the detail.

**2026-10-08 — `Kiosk.exitLockTask` gives back the whitelist, the status
bar and the keyguard; it leaves the persistent HOME and
`DISALLOW_SAFE_BOOT` in place.** The activity is `lockTaskMode=
"if_whitelisted"`, so a `stopLockTask()` on its own lasts until the next
launch, when the system pins the whitelisted task again: an "exit" that
ends at the next reboot is not one, so the owner-side exit also clears
`setLockTaskPackages` and re-enables the bar and the keyguard. The HOME
and the safe-boot restriction stay because they are what makes the
tablet Saathi's rather than what pins the screen, and a safe boot is a
way around the owner that no dialog should hand out. Every
DevicePolicyManager call is wrapped to log and continue (a
`RuntimeException`, not only `SecurityException`, because a few throw
`IllegalArgumentException` for a flag a device refuses): a kiosk that
cannot be entered is a log line, a crash would take the face with it.
`enterLockTaskIfOwner` on a non-owner build is one log line and no
`startLockTask()` -- the screen-pinning prompt is not a kiosk, and it is
not hers to answer. Lost: a kiosk launcher app, and
`lockTaskMode="always"` with no code (pins only the task; the bar and
the keyguard stay).

**2026-10-08 — The lock-task feature set is `LOCK_TASK_FEATURE_NONE`,
and YouTube's app is in the whitelist.** The brief allowed a feature
set only if the API required one; none is required, and NONE is the
one the face wants (no keyguard, no notifications, no home, no
overview, no global actions, no system info). It is written as the
value `0` in `Kiosk.kt` with a test comparing it to the SDK's constant
under the real `android.jar`, so the stub check and the Gradle test
check the same number. `com.google.android.youtube` is whitelisted
because the watch page can hand a video to the app (an "open in app"
banner, a share sheet) and a package outside the whitelist cannot start
while the task is locked -- the tap would do nothing, silently.

**2026-10-08 — The setup dialog's Save applies the kiosk box through
`Kiosk` as well as storing it, and "Exit kiosk" is shown only when the
app is not Device Owner.** Ticking a box and seeing nothing happen until
a reboot is a support call; Save enters lock task (owner only) or leaves
it, then calls back so the activity reloads the face. The explicit exit
button exists for the demo phone, where a pinned screen with the system
bars hidden has no other way out; on the owner tablet leaving the kiosk
is the checkbox's job, and a one-tap exit reachable by anyone who finds
the five-second hold is not something that tablet should have. Lost:
the same button for both.

**2026-10-08 — "Test" has an OkHttp client of its own, and shares the
address rule with Save.** The links' client has no read timeout on
purpose (a WebSocket with one dies on the first quiet minute) and the
probe wants three seconds, so the probe builds one small client, once.
`SetupDialog.probeUrl` is `EngineAddress.normalise` plus a trailing
slash, tested to agree with Save on every address, so the test cannot
pass for an address Save then refuses. Any 2xx is an answer. The
callback hops to the main thread; a dialog that has gone by then gets a
harmless update to detached views.

**2026-10-08 — `SetupDialog.show` gained a five-argument overload that
takes the two links; the three-argument form from step 1 stays and
delegates.** The brief asks Save to reconnect both links, and the step-1
signature (`show(activity, settings, onSaved)`) has no way to reach
them. `MainActivity` is another agent's file, so its call is not edited;
the integrator picks the overload. Lost: changing the three-argument
signature (breaks a call this agent must not touch) and reconnecting
through a global (a link the dialog can find without being handed one
is a link the activity no longer owns).

**2026-10-08 — `EngineService` is started and stopped by the activity's
lifecycle, is `START_NOT_STICKY`, and on API 34 does not start until
`RECORD_AUDIO` is granted.** On API 34 `startForeground` with the
`microphone` type throws `SecurityException` without the permission and
`ForegroundServiceStartNotAllowedException` when the app is not visible,
so `EngineService.start` checks the permission first (the pure rule is
`startAllowed`, tested across API 26-36) and the service stops itself if
the platform still refuses: the face stays up, the mic works while the
screen is on, logcat says why it is no more than that. Not sticky
because a service the system restarts with no activity to serve has
nothing to do and a notification to explain. The partial wake lock has
no timeout (one clock, the service's own). The notification -- "Saathi
is listening", low importance, in the shade -- is the one Android
requires of every foreground microphone service and is not the status
text under the face that CLAUDE.md forbids; the face shows nothing.
Lost: a bound service (its life is the binder's, which is the
activity's, which is the problem) and `START_STICKY`.

**2026-10-08 — `SaathiAdminReceiver` logs its callbacks and applies no
policy from them.** `onEnabled` and `onProfileProvisioningComplete` do
not both fire for `dpm set-device-owner` on every release, and a policy
whose presence depends on which path granted the owner is one that is
missing on the day it matters; `Kiosk.enterLockTaskIfOwner` applies the
whole set whenever the activity asks, idempotently. The overrides exist
so logcat answers the first three questions asked of a tablet found on
a launcher instead of the face: was the owner granted, did a lock task
begin and end, did anything ask to remove the admin.

**2026-10-08 — The kiosk strings are in `values/strings_kiosk.xml` and
the notification icon is a new `drawable/ic_notification.xml`, two files
the brief did not name.** Other components are being written in
parallel and `strings.xml` is step 1's file; Android merges every
`values/` file, so a file of this component's own cannot collide. The
icon is needed because a notification without a small icon is dropped,
and the launcher icon's filled ground would render as a white square in
the status bar.

**2026-10-08 — The kiosk component was type-checked against hand-written
stubs, as the audio component was.** No Android SDK in this container:
`Kiosk.kt`, `SaathiAdminReceiver.kt`, `BootReceiver.kt`,
`SetupDialog.kt` and `EngineService.kt` were compiled with Kotlin 2.0.21
(warnings as errors) against Java stubs of the API 26-34 shapes they
call -- `DevicePolicyManager`, `DeviceAdminReceiver` with `@NonNull`
parameters, `Service`, `Activity`, `ActivityManager`, the widgets,
`AlertDialog.Builder`, `NotificationCompat.Builder`, `PowerManager` --
plus the real okhttp 4.12.0/okio 3.6.0 jars and the step-1 pure files,
and `KioskTest`, `SetupDialogTest`, `EngineServiceTest` ran there (12
passed). That catches Kotlin mistakes and override shape; it cannot
catch a wrong constant or a platform behaviour. The first
`assembleDebug` is still the first real build.

**2026-10-08 — The target rule and ducking live in `MediaTargets`, a
pure class on the two interfaces, not in `MainActivity`.** The engine
addresses pause/resume/stop/volume/layout to "whichever target is
playing" and never names it, so this side remembers which target the
last `play` went to and hands a frame to the pane only while the pane
is that target; ducking (20% while listening/thinking/speaking/handoff)
is applied through the same memory. In the activity none of that is
testable without a device; as its own class it implements
`EngineLink.Listener` and `YouTubePane.Listener` and `MediaTargetsTest`
pins it on the JVM against fakes of both interfaces (14 cases: an embed
play never reaches the pane, nor does any control frame while the embed
is the target; `stop` and the pane's own `ended`/`error` clear the
memory; a late `ended` from the pane after the embed took over does
not). Lost: stopping the pane on an embed play "to be safe" -- `media.py`'s
`_play` already emits a `stop` before a play that changes target, and
a shell that second-guessed that would stop the pane twice on every
switch. Lost: asking the engine to name the target on every frame, a
protocol change for a rule one side can keep, as the face page keeps
it. One thing the engine did not say: a browser play that arrives
without `watch_url` is opened at the canonical watch URL for its id
(the string `media.py`'s `watch_url()` builds), so an older engine
still plays rather than being refused.

**2026-10-08 — The kiosk is entered on every resume only when the setup
dialog's box is ticked.** The brief said `enterLockTaskIfOwner` in
`onResume` unconditionally; that would make the dialog's checkbox a
lie -- unticking it exits lock task and the next resume would pin the
screen again. `settings.kiosk` gates it, matching what Save does and
what step 1's marker said ("when settings.kiosk and the app is Device
Owner"). On the owner tablet with the box ticked every resume re-applies
the whole policy set, idempotently, so a reboot lands on the face
locked.

**2026-10-08 — The face WebView reloads on the links' schedule when the
engine does not answer, refuses to leave the engine's address, and
recreates the activity when its renderer dies.** A tablet that boots
before the engine must show the face once the engine is up without
anyone touching it: a main-frame load error or HTTP error schedules a
reload after `ReconnectBackoff` (500 ms doubling to 30 s, `main.js`'s
numbers, so the page and the sockets come back in step), reset by a
load that finishes without an error; one retry per load. Chromium's own
"webpage not available" page shows in between -- lost: a native "engine
not found" screen, which would be status text on the face's screen, and
Chromium's page already says in words what is wrong. Main-frame
navigations not under the stored address are refused
(`EngineAddress.isOn`, pinned in `EngineAddressTest`): the embed
player's "watch on YouTube" link would otherwise replace the face with
youtube.com in this WebView, and the watch page has its own pane. On
`onRenderProcessGone` the activity calls `recreate()` and returns true:
both WebViews share the renderer, so a fresh activity is a fresh face, a
fresh pane and fresh links in one step. Lost: returning false (the
system kills the process -- the owner tablet relaunches its HOME, the
demo phone drops to the launcher) and rebuilding the two WebViews in
place (a second recovery path for the same event). Known cost: the
pane's own `onRenderProcessGone` fires first and reports the open video
as a browser failure, so a renderer crash mid-video refuses that video
for the session; the recreate restarts everything else.

**2026-10-08 — The microphone permission is asked for at start through
the activity-result contract, and the grant starts `EngineService`.**
Asking on the first hold would put a prompt in the middle of her first
sentence; `onRequestPermissionsResult` is deprecated in
`ComponentActivity` for the same contract. On API 34 the microphone
service cannot start until the permission is granted, so the callback
starts it if the activity is started by then; `EngineService.start`
itself stays the one place that checks. One `OkHttpClient` for the
process (a lazy in `MainActivity`'s companion, built by
`OkHttpEngineLink.newClient()` for its ping interval) rather than one
per activity instance, so a recreate does not leave a second pool
behind. The back button is swallowed by an `OnBackPressedCallback`
rather than an `onBackPressed` override (deprecated since API 33 and
bypassed by predictive back).

**2026-10-08 — The whole `android/` tree was type-checked at once
against a merged stub set; the YouTube sign-in button is still not
offered.** The three earlier stub sets (kiosk, audio, pane) were merged
and extended with the AppCompat/activity/core shapes `MainActivity`
uses; every main file and every test compiled under Kotlin 2.0.21 with
warnings as errors, and all 87 JUnit tests ran green on the JVM. What
that cannot catch is recorded in `android/README.md`. `WebViewYouTubePane.
signIn()` exists for the device's Google account; the setup dialog's
three buttons are taken (Save, Cancel, Exit kiosk) and a fourth control
was not added in this step -- the pane plays signed out, and the README
says so. Lost: adding it now, one more untested widget in the one
dialog that already carries the kiosk switch.

**2026-10-08 — Review pass over the Android port, engine side: both
sockets have a heartbeat, a vanished `/ws` client is released, a
replaced `/audio` client releases the play it was waiting on, and
reports are routed by target.** Five findings, each verified by reading
before it was fixed. (1) `web.WebSocketResponse()` had aiohttp's
default `heartbeat=None` on `/audio` and `/ws`, so a phone that died
without a close frame stayed "attached" for as long as the kernel kept
the TCP connection: every press used the dead remote mic, every
sentence waited its bound into the void, and the local mic and speaker
never came back. Both sockets now ping every 5 s
(`_HEARTBEAT_SECONDS`); a missed pong closes the socket and the
handler's `finally` detaches the client. Lost: detaching on a play's
timeout (a slow phone is not a dead one; the heartbeat is the right
instrument). (2) `RemoteAudio.attach()` replacing a client left
`_current` in place, and the replaced handler's late `detach` found it
was no longer the client and returned early, so a reconnect
mid-sentence (the app resumed, a Wi-Fi blip) cost the WAV's length plus
3 s with her face on "speaking"; `attach` now releases it as `detach`
does. The sentence is lost, not re-sent: the new client never saw its
header. (3) A `/ws` socket that *vanished* with its press down (no
close frame, 1006; or a page going away, 1001) is released for on the
way out, through the same `handle_input` every real press and release
goes through -- the shell drops a `release` it cannot send and never
re-sends it, so core.py sat in LISTENING with the capture open until
her next full hold, whose press was a no-op. A socket that closed in
order (1000) is taken at its word: the shell always releases before it
disconnects, and the existing tests close their socket mid-press and
expect the press to stand, which is the right rule, not a test
accommodation -- a client that could say goodbye could have said
release. (4) `reset` and `ended` were honoured from any client: the
face page sends `reset` on every socket open it has no player for, so
a reconnect while the shell's watch page played un-played it, "carry
on" restarted the video from the top, and the next embed play skipped
the `stop` the target switch relies on. A `media_event` may now name
its player (`target`: "embed" or "browser"; absent means the face
page, whose frames predate the field), a `reset` counts only from the
player whose target is playing, and an `ended` naming a video other
than the one playing is a late report. The shell sends `reset` with
`target: "browser"` on every `/ws` open with nothing playing, as the
panel does, so a restarted shell is reported too. Lost: gating a bare
`ended` by source as well -- the panel never sends one while the
browser target plays (its view is "none" after the `stop`), and two
existing tests send one while the browser plays and expect it honoured;
they are right about today's clients. (5) With no client that can show
the watch page connected -- the Pi kiosk with the face page alone,
which is the production configuration, not only the laptop demo the
entry above allowed for -- an embed refusal re-emitted the play on the
browser target, nobody took it, and the controller answered "it's
already playing" to a blank panel. `MediaController` takes a
`browser_available` callable and, when it says no, treats the embed's
refusal as the browser's verdict would be: refused for the session and
the rest re-offered on a card, which is what happened before the target
rule. `cli.py` hands it `remote_audio.attached` -- the Android shell is
the one client with a watch page and its `/audio` socket is the one
sign of it the engine already has. Lost: a `hello` on `/ws` (a protocol
change the face page would also have to make, for a fact one existing
seam already knows) and treating the panel's own "api"/"no_ready"
deadlines as transient rather than marking the video: the finding is
fair -- they are the network's failure -- but the rule that every
non-browser code is the embed's is pinned by
`test_every_error_code_but_the_browsers_own_is_the_embeds`, and a test
is not changed to pass; raised here instead, for a later pass that
retries those two codes once. The panel, for its part, now remembers
which target the last play went to (`activeTarget`) and lets
pause/resume/volume reach its player only while that is the embed: the
hidden, stopped player kept after a target switch answered her "carry
on" with the previous video's audio under the watch page.

**2026-10-08 — Review pass, tests the engine side lacked.** Added, not
changed: the contract's number itself (`DEFAULT_GRACE_SECONDS == 3.0`),
a client replaced mid-play, a press with no local capture source held
through the phone and its inverse (no source, no phone: nothing
starts), a client that never acks still letting the state machine reach
idle, the four bad `/audio` text frames (a wrong sample rate, non-JSON,
an unknown type, a non-string `played` id) leaving the client attached,
a quiet client detached by the heartbeat, a vanished `/ws` client
released and a bystander not, a hold socket vanishing mid-hold, a
`reset` named by target through the server, an embed refusal with no
`/audio` client re-offered rather than sent to the browser, and a
Chromium scenario for the panel's control gating. Two harness facts
worth writing down: aiohttp's test client answers pings only while it
is inside `receive()`, so a socket left idle at a short heartbeat is
dropped too (the tests keep a live socket reading while they wait), and
`close()` on a socket the server has already torn down raises, so a
quiet socket is drained to its close instead. The suite was run on
x86_64 only (`uname -m`); CLAUDE.md's "Done means" asks for arm64 as
well, and no arm64 machine was reachable from this container. The
`/audio` path and the fixes above have no architecture-specific code,
so this is a verification gap to close on the Pi before the engine side
is called done, not a suspected failure.

**2026-10-08 — Review pass over the Android shell: what each finding
changed, and what was left.** The sign-in path is gone
(`WebViewYouTubePane.signIn()`, its state and the README's promise of a
button): Google refuses sign-in from an embedded browser, the pane
wears a desktop user agent, and the only way that page could have
worked was by getting past the refusal -- the circumvention the
product's own "legal way" rule forbids and the thing that puts a device
account at risk. The pane plays signed out; `WatchPage.SIGN_IN_URL`
stays as the canonical wall for the host rule and its test. The
three-identity rule is now in the README, where the person signing
things into a tablet reads. The `/ws` `release` is sent after the mic's
last frame: `AudioLink.stopSending` takes a callback the mic thread
runs once the capture has sent its tail, and `PushToTalk.release` sends
the release from it -- the engine stops forwarding the instant it reads
the release and the two sockets give no ordering guarantee, so the
first draft's order (release, then stop the mic) lost the end of every
sentence; lost: an engine-side grace after a remote-mic release, which
would put 150 ms on every turn's hot path. A hold while the `/audio`
socket is down opens no microphone (one log line); the first draft lit
the privacy indicator for frames `sendFrame` dropped. The `/audio`
link's `Request.Builder().url()` has the `IllegalArgumentException`
guard the `/ws` link had, since the reconnect runnable runs on the main
looper. `HoldListener.cancel()` exists and the activity calls it in
`onPause`/`onDestroy`, so a renderer crash recreating the activity
mid-hold cannot open the dialog on a finished window. A drop of `/ws`
ends a hold on the shell's side too (`MainActivity.LinkEvents`, which
delegates everything else to `MediaTargets`), matching the engine's
release of a vanished socket. On a first run nothing connects until an
address is saved: `Settings.engineUrl` is null until then and the
dialog opens itself; `EngineAddress.DEFAULT` is the field's hint and
nothing else (the first draft reconnected forever to a placeholder
that, on a common home network, is a stranger's device). The
fullscreen layout keeps the face as a corner cell (22% of the screen
each way, bottom-left, the face page's `22vw x 22vh`) with the pane
taking the rest of the row; the first draft set the face container
`GONE`, which made the engine's own "your face is in the corner" untrue
and broke SPEC.md's "the face is in every layout" for this target;
lost: a true overlay, which means re-parenting a WebView. `stop()`
forgets the layout, since the engine sends `fullscreen` with every
play. The install script tells the bridge when the `<video>` is found
(`onVideo`) and the pane answers with the level it holds then: the
element can appear 30 s after the script was built with the level of
that moment, and a ducking change in between was lost until her next
press. Navigation in the pane requires https (`isWatchUrl`, the same
rule `open()` applies), and `network_security_config.xml` denies the
YouTube hosts cleartext -- what the config can express, beside the LAN
rule it cannot. `EngineService` is started with `startService`, not
`startForegroundService`: the latter promises a `startForeground()`
within seconds and kills the process when the service stops without
one, so the first draft's `stopSelf()` fallback was a crash, not the
degrade its header described; from a visible activity a plain start is
allowed and the fallback is real. Its header now says what it does
(keeps capture and the wake lock across a paused-but-visible activity;
`onStop` ends it), not what the first draft claimed (a dimmed kiosk's
whole life). The notification is titled "Saathi": the first draft's
"Saathi is listening" was a status label and a false one most of the
time. `BootReceiver` does nothing on Android 10+ (the system ignores a
background activity start by an ordinary app; the owner tablet is HOME
and needs no receiver) and says so in its header and the README; it
still serves an Android 8/9 phone, which is why it was kept. The debug
manifest overlay sets `android:testOnly` so `dpm remove-active-admin`
works for the build the README tells you to make (it never did: only
Android Studio's Run button injects the flag), at the cost of `adb
install -t`. `androidx.webkit` and `kotlinx-coroutines-android` are
gone from the dependencies: nothing imported them.

**2026-10-08 — What the review pass could and could not verify on the
Android side.** The earlier merged stub set was not kept, so a smaller
one was rebuilt in a scratch Gradle project: eleven `android.webkit`
shadow classes (the API 21-26 shapes the pane uses, with the API-26
getters that Kotlin's property syntax needs) and
`androidx.core.content.ContextCompat`, ahead of the Maven `android`
API-16 jar and the real okhttp/okio jars. Thirteen main files --
`Protocol`, `EngineLink`, `AudioLink`, `YouTubePane`, `EngineAddress`,
`OkHttpEngineLink`, `OkHttpAudioLink`, `PushToTalk`, `WatchPage`,
`MediaTargets`, `WavHeader`, `Settings`, `WebViewYouTubePane` -- and a
mirror of `MainActivity`'s delegation and call shapes compiled under
Kotlin 2.0.21 with warnings as errors, and 79 JUnit tests in ten
classes passed (`MediaTargetsTest` 16, `ProtocolTest` 16,
`WatchPageTest` 13, `WavHeaderTest` 11, and the rest). Not compiled
again: `MainActivity`, `SetupDialog`, `EngineService`, `BootReceiver`
(androidx, `R` and API 26-34 platform shapes the small stub set lacks),
whose edits were read twice instead; their three test classes (12
tests) were not re-run. The first `assembleDebug` remains the first
real build, as the README says.
