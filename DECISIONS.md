# Decisions

Retroactive record of calls made without an explicit instruction —
substitutions, defaults, and judgment calls — with one line of reasoning
each. New entries go at the bottom, dated. Superseded entries are struck
through, not deleted, so the history of *why* something changed stays
readable.

---
2026-10-07 — Livelier eyes: listening breathes, nods and catches the light; the wake word perks them up. The first demo's listening eyes read as a held pose, not attention. roboeyes.js gained an expressiveness layer on top of its easing model (breathing, head tilt, nods, sparkle, brightness, warmth lid, perk and squint, slow blink, a confined drift region), and eyes-face.js gives every state a living version of its look, with a distinct perk-up for ATTENTIVE. Lids are now cut out of an offscreen layer and the glow is drawn from what's left; painting lids in the ground colour left the glow outlining the hidden part, so smiles read as dark bowls. A blink now reopens to the current size instead of idle size. Lost: CSS transforms on the canvas (they bypass the model's easing and its tests) and pupils (gaze already moves the whole eye; a pupil would have to look somewhere too).

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

**2026-10-07 — Live captions while the key is held; silent turns say
why in the log.** Live: spacebar presses ended with no reply and no
caption, and nothing on screen or in the log said whether the mic was
heard. With captions on, the server now re-transcribes the buffer so
far every second while LISTENING (`CascadeSession.preview_heard()`,
gated by the same Silero check, outside the `VoiceSession` Protocol and
reached by getattr) and sends it as the "Heard" line. It costs one STT
call per second held, and only while captions are on. A turn that ends
silently now logs which gate stopped it: no speech (with seconds of
audio and RMS) or an empty transcript. Lost: a mic level meter (status,
not content) and streaming STT (a new client for a debugging aid).

**2026-10-07 — Echo-cancel pair found by module arguments on PipeWire.**
Live: the 800-series laptop runs PipeWire 1.6, whose pulse server sets
neither `device.class = "filter"` nor `device.master_device` on
module-echo-cancel's nodes. `SystemEchoCancel.find()` never matched, so
every `saathi run` loaded another module, failed to find it, and
started with no voice engine — the spacebar moved the face and recorded
nothing. `find()` now falls back to the module whose own
`source_master`/`sink_master` arguments name the pair, and takes the
nodes it owns. Lost: matching PipeWire's `node.link-group` (a naming
convention, not the pair's identity).

**2026-10-07 — "Saathi" wake word: local whisper keyword spotting, not
openWakeWord.** SPEC.md names openWakeWord as the across-the-room
trigger, but it has no "Saathi": a new word means training a custom
model, and its pretrained models are CC BY-NC-SA. `audio/wake.py`
keeps the echo-cancelled mic open while idle, cuts utterances with the
Silero VAD already here, and transcribes each one on-device with
faster-whisper `tiny.en` (~180 ms; base 3x slower and dropped the name
from "Saathi, play a song", small.en 5x slower and no better). Fuzzy
match at 0.82 over the first three words: takes Sathi/Sothai/Saarthi,
rejects sahi/sath/sorry. Nothing leaves the device until the name is
heard. Her name alone: IDLE -> ATTENTIVE (0.35 s, the eyes perk up) ->
LISTENING, ended by a VAD endpointer (0.9 s pause, 5 s of nothing, 15 s
cap). Name plus request in one breath: that audio is the turn.
faster-whisper moved from the `hardware` group to a default dependency.
Off with SAATHI_WAKE_WORD=off; model via SAATHI_WAKE_MODEL. Not done:
the open-mic window after Saathi speaks (SPEC.md, state machine), so a
follow-up still needs the name or the key.

**2026-10-07 — Livelier eyes: listening breathes, nods and catches the
light; the wake word perks them up.** The first demo's listening eyes
read as a held pose, not attention. `roboeyes.js` gained an
expressiveness layer on its easing model (breathing, head tilt, nods,
sparkle, brightness, warmth lid, perk and squint, slow blink, confined
drift region) and `eyes-face.js` gives every state a living version of
its look, with a distinct perk-up for ATTENTIVE. Lids are now cut out of
an offscreen layer and the glow drawn from what's left: painting lids in
the ground colour left the glow outlining the hidden part, so smiles read
as dark bowls. A blink reopens at the current size, not idle size. Lost:
CSS transforms on the canvas (they bypass the model's easing and its
tests) and pupils (gaze already moves the whole eye).

**2026-10-07 — Latency: Groq hears her, OpenAI replies; shorter wake
pauses.** Turns were taking 2–4.5 s from end of speech to voice
(`turns`: STT 0.7–1.8 s, reply 1–2.4 s). Measured from the demo
laptop: Groq whisper-large-v3-turbo 270 ms vs gpt-transcribe 731 ms
median on the same clip. With both keys set, STT now goes to Groq (no
retry, 4 s deadline) and falls back to OpenAI's transcriber on any
failure; the reply model is unchanged. Off with SAATHI_STT_PROVIDER=openai.
Wake word: the pause that ends "Saathi" is 0.3 s (was 0.5) and a
hands-free turn ends after 0.8 s (was 0.9 — shorter cuts off a pause
mid-sentence). The audio heard while the name was being checked now
starts the turn; before, the first words after the name were lost,
which is likely why several hands-free turns came back with an empty
transcript. Not done, needs a decision: streaming the reply (first
words ~640 ms vs whole answer ~1080 ms for gpt-4.1) changes the
`VoiceSession` contract. gpt-4.1-mini was not faster to first token
(597 vs 637 ms) and missed the music tool 4/4 in the earlier bake-off.

**2026-10-07 — Big, playful eyes: visitors, z's, blush and other
feelings (owner's explicit ask, past SPEC.md's caution).** SPEC.md warns
that eyebrows "read as children's illustration, which lands as
patronising". The product owner asked for the opposite direction on
purpose: eyes "quite big", filling most of the screen, a ball / leaf /
star now and then that the eyes follow while idle, floating z's when
asleep, and emotions — blush, happy, sad, surprised (plus love and
curious). Lost: "warmth only" (narrowing at the corners and nothing
more) — dignified, but flat in the demo. Kept from the caution: still no
mouth, no eyebrows, no status words; a "z" is drawn, not a label.
Visitors are rare (14–30 s apart), only while IDLE and only when the
face has the full screen, and off under prefers-reduced-motion.
Size: the model keeps its 132-unit eyes (its tests and easing are in
those units); the renderer scales by `fitScale(box)` from
`eyes-layout.js` and clamps gaze so the pair never leaves its box.
Lost: resizing the model's eyes per container (every target and blink
threshold recomputed mid-animation). The canvas follows its container
by ResizeObserver, which is what fixes the fullscreen-video corner
(it was stretched to the corner's shape with 132 px eyes cropped in it).

**2026-10-07 — Emotions go core-side -> face; `onEmotion` is an
optional Face method. Owner signed off on the Face interface change
(2026-10-07).**
`screen/emotion.py`'s `EmotionController.show(name, seconds)` is the
one door: validated against a fixed set, clamped 0.5–15 s, broadcast
as `{"type": "emotion"}` through the server's existing seam, lapsing
back to the state's look in the browser. The face receives it through
`onEmotion(name, seconds)`, documented in `face.js` as *optional* so
orb and ink stay valid untouched — but it is still an addition to one
of the five interfaces; the owner confirmed it the same day. Lost: a window
CustomEvent the eyes listen for (no interface change on paper, a hidden
one in practice). Not wired to the model: *when* to blush belongs to the
voice side, and an `express` tool would spend the one tool call per
turn (DECISIONS 2026-09-18) and change what she hears — so nothing
calls `show()` yet except tests and `?demo=emotion-<name>`.

**2026-10-07 — Phone panel on the right during a call; End call goes
through the hold seam.** `call/controller.py` now reports a `CallView`
(name, number, calling/ringing/connected, answered-at) to
`screen/call_panel.py`, which broadcasts `{"type": "call"}` and re-sends
it on connect; ringing comes from the ring watcher's existing poll. The
face moves to the left 60% and is fitted there. The red button sends
`{"type": "call_hangup", "id"}`; the server checks the id against the
call on screen and calls `HoldController.complete()`, which fires the
same registered handler as a two-second spacebar hold — there is no
second hang-up path, and with no handler registered the tap does
nothing. The status word is on the phone panel, not under the face;
lost: no word at all, because a blank panel before the far end answers
reads as broken. `dial()` gained an optional `who` for the name.

**2026-10-08 — Calling family goes over a free family app (WebRTC +
Web Push); Twilio stays only as the fallback for people who aren't
paired.** Product owner's decision, on cost: international per-minute
PSTN is what this product's users would make most. Lost: **Twilio PSTN**
as the main path (≈$0.06/min to a Singapore mobile, more elsewhere,
billed per started minute — `docs/completed/calling.md`); **WhatsApp
Business Calling** (per-minute pricing as well, plus Meta business
verification and a WhatsApp Business account per device); **a cheaper
SIP trunk** (lower, but still per-minute, plus a registrar and NAT
handling on the device). What was built, in `saathi/call/`:
`webrtc.py` (signaling state machine), `family.py` (pairing),
`push.py` (Web Push), `ice.py` (STUN/TURN config), `family_server.py`
(the PWA's server), `routing.py` (paired vs Twilio),
`family_runtime.py` (wiring); the PWA in `screen/static/family/`; the
kiosk end in `screen/static/js/family-call.js`.

**2026-10-08 — The kiosk's Chromium is the device's WebRTC end, on the
system default mic and speaker.** Python does signaling and validation
only; media never passes through it. Lost: aiortc in-process (a large
native dependency whose aarch64 builds need libav/libvpx, and a second
audio path to keep echo-free). Chromium's own echo canceller has the far
end as its reference because Chromium plays it, which `aec.py`'s module
would not (that pair is not the system default — the 2026-09-25 diff to
make it so is still unapplied). Routing Chromium's streams onto the
echo-cancel pair with `pactl move-*` lost too: double AEC, and finding
"Chromium's stream" means matching a process. No device is named
anywhere. The kiosk script gains `--use-fake-ui-for-media-stream` (it
accepts the mic prompt; it does not fake the device).

**2026-10-08 — Pairings live in `family.sqlite3`, owned by the call
module, not in `IdentityStore`.** Its schema is closed (`append()`
raises `UnknownTable`), so a table there is an interface change; and
writing a pairing into `entities.notes` would make a new latest-wins
"Priya" row with no phone, hiding her saved number. Joined to her
contacts by normalised name. Secrets (pairing tokens, member keys) are
stored as SHA-256 of 32 random bytes; tokens are single-use and expire
in 10 minutes. Pairing does not write her memory: it is device setup a
relative did, not something she said.

**2026-10-08 — Web Push hand-written over `pycryptodomex` (now a named
dependency), not `pywebpush`.** pywebpush pulls requests, http_ece,
py-vapid and `cryptography` for one ECDH, one HKDF, one AES-GCM seal
and one ES256 signature; pycryptodomex was already installed (yt-dlp's
extras). Pinned byte-for-byte to RFC 8291's worked example. VAPID keys
are made once (`vapid_private.pem`, 0600) and never rewritten —
regenerating would orphan every phone's subscription. `segno` (pure
Python, BSD) added for the pairing QR.

**2026-10-08 — The family app is opt-in (`SAATHI_FAMILY_APP=on`).**
On by default lost: it puts a server on the internet through the
tunnel, and that should be a line someone wrote, not a side effect of
updating the code. It also kept `tests/test_cli.py`'s Twilio-only
wiring tests true as written. With it off, calling is exactly as before.

**2026-10-08 — A stable URL is configuration; the quick tunnel is the
labelled fallback.** `SAATHI_PUBLIC_URL` (https, e.g. a named
cloudflared tunnel) is used as-is. Unset, a quick tunnel to the family
port (8769) starts at boot, and the pairing screen says a pairing made
now lasts only until restart. Mitigation, not a fix: each ring's push
carries the *current* URL and a per-call token, so a phone paired on an
old hostname can still answer (and decline from the notification); it
can't start a call until re-paired. The family server is a separate
app on its own port and loop from the screen server — the screen's
socket moves her face and writes her preferences and must never be
tunnelled — and the pairing QR is minted only on the screen server
(loopback, and refused if a `Cf-*` header shows it came through a
tunnel).

**2026-10-08 — ICE servers are config, STUN-only by default.**
`SAATHI_ICE_SERVERS` (JSON list) or Cloudflare TURN credentials minted
per call from `SAATHI_TURN_CLOUDFLARE_KEY_ID`/`_API_TOKEN` (the token
stays on the device; what reaches a phone expires in 24 h). Nothing was
signed up for. A call whose ICE hasn't connected in 30 s ends, and the
log names TURN.

**2026-10-08 — Incoming calls ask with a Confirm card and answer on a
0.2 s hold, never on their own.** The card ("Priya is calling.
Answer?") takes a tap, or her "yes" through the existing
`answer_card` tool (the incoming flow is one of its flows). The
spacebar is the hold seam with a 0.2 s hold, so a press during a ring
never starts a turn; a shorter tap is abandoned and the card re-shown.
The phone panel appears only once answered: during a ring its End
button would fire the hold handler, which is *answer* — a trap. Ringing
is a two-tone in the kiosk page and a "surprised" emotion; connected is
"happy". After answering the hold handler is the same hang-up as
Twilio's, so the panel's End button and a two-second hold behave
identically for both kinds of call. The device is always the WebRTC
offerer, in both directions.

**2026-10-08 — With the family app on, `call_contact` gates per path,
not as a whole.** A paired daughter can be rung while the Twilio relay
is still resolving, and vice versa; with it off the old whole-tool gate
(2026-09-26) is kept unchanged.

**2026-10-08 — Audio arrived in 2-second bursts; fixed at `parec`.**
`audio/capture.py` started `parec` with no latency, so the sound server
(PipeWire's pulse, `pulse.default.frag` = 2 s) handed over audio in
64,000-byte bursts every 2.0 s (measured; with `--latency-msec=20`, 640
bytes every ~20 ms). That one default explained three complaints: the
wake word saw "Saathi" end 0-2 s late, a hands-free turn's endpointer
could only end on a burst boundary (`turns.eou_ms` 2,027 / 4,020-4,025 /
6,022 / 8,021-8,026 / 16,023), and a spacebar release killed `parec`
with up to 2 s of her last words undelivered. `parec` now always asks
for 20 ms. Lost: reading bigger chunks on our side (the delay is the
server's fragment, not our read). `turns.eou_ms` keeps its meaning (time
from the turn's start to its end; tests pin it); the hands-free wait
after her last word is logged instead ("ended N ms after her last word").

**2026-10-08 — The name is heard while she is still speaking; the turn
streams from the listener's own mic.** `audio/wake.py` also checks an
utterance in progress at 0.55/0.9/1.3 s after speech onset (skipped when
tiny.en is busy). Measured with the real listener, Silero, tiny.en and
endpointer over 97 clips (Piper amy/pratham, Google en-IN Chirp3 Aoede/
Charon and Neural2, normal and 1.25x speed), simulated clock = audio +
compute: the device wakes a median 0.50 s *before* she finishes (was
0.33 s after the utterance's end, plus 0-2 s of bursts: ~1.3 s mean
after). The wake hands the turn its own mic (`follow()`): no gap, no
second `parec` mid-sentence. "Sati" added as a known spelling (tiny.en's
usual one on Indian voices): whole-clip recall 35/56 -> 56/56, false
wakes on deliberately close negatives ("Sahi hai", "Saath chalo",
"Satisfied", "Sorry about that") unchanged at 7/41; with early checks
the flow gives 55/56 and 14/41 (12/41 before) -- these negatives are
near-homophones and a false wake costs one silent or short turn. Lost:
dropping whisper's "Saathi" prompt (0 false wakes but recall 6/56) and a
richer prompt (56/56 but 17/41 false).

**2026-10-08 — A one-breath command ends at its short pause.** A turn
woken early ends at the endpointer's 0.8 s pause, but the listener
keeps cutting it at 0.3 s and asks `router.sounds_complete()` whether
the on-device words already make a routed command ("call Udi"; tiny.en's
"Kaul"/"Kol" read as "call"; never a question, never "call my..."). If
so it ends there. Measured: turn end after her last word, median 0.85 s,
0.33-0.39 s for the call clips the local transcript got right (9 of
20). Only *when* to stop is local; the words still come from cloud STT.
Lost: always ending at 0.3 s (the old one-breath path) -- "Saathi,
call... Udhi" with a breath in it lost the name; "Saathi, what's the
time?" now waits 0.8 s, not 0.3 s. The router also drops a leading name
however spelled (`strip_wake_word`): cloud STT wrote "Sothai, call
Udhi" and the command fell through to the model.

**2026-10-08 — Warm the AI connections when a turn starts.** Measured:
the first OpenAI call after >=70 s idle took 840-990 ms vs 610-800 warm;
Groq ~430 vs ~230. Turns are usually minutes apart, so nearly every
real turn paid the TLS handshake twice. `CascadeSession.start()` now
sends a free `GET /models` on each client (if unused for 30 s) while
she speaks. Lost: a periodic keep-alive (requests all day for the same
saving).

**2026-10-08 — gpt-4.1 stays the reply model.** Re-ran the bake-off,
streamed, with all six tools, the real compiled context and one prior
exchange, eight prompts (two music, two calls, set_language, three
chat) x 6: every model called the right tool every time (gpt-4.1-mini
too, unlike the earlier 0/4). Median whole-reply time, chat / tool:
gpt-4.1 944 / 874 ms, gpt-4o 874 / 854, gpt-4.1-mini 930 / 794,
gpt-5.4-nano 970 / 860, gpt-4o-mini 947 / 870 (an earlier run: 5.4-mini
1268, 5.5 2080, 5.6-luna 1462; gpt-6.1-sol refuses reasoning "none").
Within ~70 ms of each other -- noise against the network floor -- so not
worth a change in how she sounds. Groq's qwen3.8-27b took 13-14 s (rate
limited) and missed `call_contact` 4/4; gpt-oss-120b refuses reasoning
"none". First words vs whole reply for gpt-4.1: 801 vs 944 ms here, more
for production's longer replies; that gap needs a streaming reply, which
still changes the `VoiceSession` contract -- not done.

**2026-10-08 — The reply's mood reaches the face via a tag.** With the
face's EmotionController, `cascade.py` asks the model to start each
reply with a mood tag ("[happy] ..."), strips it before anything speaks,
captions or remembers the text, and hands it to `on_mood()` (cli.py:
`EmotionController.show`, which refuses any look it doesn't draw).
Outside the `VoiceSession` Protocol, like `preview_heard`. Measured on
gpt-4.1 (8 prompts x 6): tagged 18/18 chat replies, tools still 48/48,
whole reply 1037 ms tagged vs 944-1190 untagged (noise). Lost: a tool
(spends the turn's one call and a round trip; the registry is exactly
what the model is offered) and a second classifier call.

**2026-10-08 — The conversation stays open after a reply.** SPEC.md
already said follow-ups need no trigger; nothing did it -- every reply
ended at IDLE and the wake listener only listens at IDLE, so each
follow-up needed "Saathi" again. Now a spoken reply ends in ATTENTIVE
(`(SPEAKING, "follow_up")`, new in core.py) with `audio/listen_window.py`
on the echo-cancelled mic: the first 0.35 s after `say()` returns are
dropped unheard (her own voice's tail), speech must start within 7 s
(`SAATHI_FOLLOW_UP_SECONDS`, `off` to disable) and needs ~100 ms of
consecutive VAD speech to count, then the same 0.8 s endpointer ends the
turn; a quiet window is `dismiss` -> IDLE. Nothing leaves the device
until she starts. The spacebar works inside the window
(`(ATTENTIVE, "press")`). Not opened after a reply that changed what is
playing while it plays (the song should be heard, not held ducked), nor
during a call. Lost: `done` -> IDLE -> `notice` (two broadcasts: the
face blinked and the song un-ducked for a frame); reusing the wake
listener's mic (it only listens at IDLE and only for the name).

**2026-10-08 — Her name is the trigger, never the content.** Live,
"Saathi" alone woke the device, the early-wake hand-over put the name's
audio into the turn, cloud STT wrote "Saudi?", and the model answered
it. The cascade now strips a leading name from the transcript before the
router and the model (`strip_wake_word`, looping, after "hey"); on a turn
that began with the name (`started_by_name()`, outside the Protocol) the
cloud's other spellings count too ("Saudi", "Sorry", "Saadi"...) --
never on a spacebar turn, where "Sorry?" means "pardon?". A transcript
that is only the name, or a by-name turn with no speech, is no turn: no
model call, `(THINKING, "name_only") -> ATTENTIVE`, listening; if she
stays quiet 3.5 s (counted from when she went quiet) Saathi says a short
fixed line in her language ("Yes?", "I'm here.", ... rotated; never
"Sure!"), then the open window as after any reply. Lost: asking the model
for that line (a round trip for a word); a fixed spelling list in the
wake matcher itself (it would wake on "Saudi Arabia" and "sorry").

**2026-10-08 — A search plays the most relevant result directly.** "Play
Shape of You" offered three cards that were all Ed Sheeran's Shape of
You. Owner's rule, `tools/media.py`'s `choose_result`: read (song,
artist) from the raw title and channel (the part matching the channel
is the artist, else the part made only of her words is the song, else
"Artist - Song"); fold same-song-same-artist versions into the top one
and play it at once with nothing said; ask (the existing card) only when
the top results are one title by different artists ("Hello": Adele or
Lionel Richie). An unknown artist never makes it ambiguous. The rule is
injected (`MediaController(choose=...)`, wired by cli.py); the
controller's default remains ask-every-time, which is what the 23
existing media/card tests describe -- the card mechanism the ambiguous
case still uses. Volume changes now answer with silence (`say: ""`): the
louder song is the answer, and no reply sits on top of it ("already as
loud as it goes" still goes to the model). Lost: a model call to judge
sameness; asking whenever there was more than one result.

**2026-10-08 — Media commands are favoured while music plays.** The
engine reads one boolean, `media_playing()` (cli.py: the controller's
`playing`), never the controller. While it is true: the cloud transcriber
gets a `prompt` listing the commands; the wake listener's tiny.en gets
the same bias, and `sounds_complete` treats "stop"/"louder"/"next" as
whole commands so "Saathi, stop" ends at its 0.3 s pause; the router
matches media phrases even with no search offered and takes a media
command right after the name wherever the name falls ("...your body,
Saathi, stop"). More phrasings added (reduce/increase the volume, next
song, ...). Lost: matching "stop" anywhere in a mid-song transcript
("don't stop", lyrics).

**2026-10-08 — The song no longer reaches her request.** Two causes.
(1) The browser only ducked at listening/thinking/speaking, and the wake
word's moment (ATTENTIVE) -- when the request after the name is already
streaming in -- played at full volume. ATTENTIVE now ducks (it is also
the whole open window), and the duck is a tenth, not a fifth. Same
core-state message path as before; tests/test_media_policy.py and
tests/test_media_panel.py had pinned "attentive plays at full volume"
and the 0.2 factor, the decision the owner reversed -- their numbers were
updated to the new rule, nothing else. (2) The browser played to the
hardware default sink, so the echo canceller never had the music as its
reference. `saathi run` now makes the echo-cancel sink the default while
it runs and puts the previous default back on exit (`aec.py`'s
`route_default_through`; ids come from PulseAudio and `devices.py`,
never from code). Safe because: only a sink the server lists is used;
restore runs once, only if nobody changed the default since, and after a
crash that left it routed it restores to the detected speaker; Saathi's
own voice and call audio already play to that sink explicitly; PipeWire
falls back by itself if the module goes away. Off with
`SAATHI_AEC_DEFAULT_SINK=off`. Not yet measured live (the running
instance was not touched). Lost: `move-sink-input` on the browser's
stream (it changes with every video and would have to be chased).

**2026-10-08 — Her name starts every turn; the open window after a reply
is off.** Owner's call, live: a maths lecture playing in the room was
answered seven turns running -- each reply reopened the 7 s window and
each sentence from the video counted as her follow-up. `saathi run` now
passes `open_after_reply=False` (screen/server.py); a reply ends at IDLE
and only "Saathi" or the spacebar starts the next turn.
`SAATHI_OPEN_CONVERSATION=on` brings the old window back. The window
after her name alone stays ("Saathi" ... "Yes?" -> she answers without
the name): there she has just addressed Saathi. SPEC.md's Triggers line
amended to match. Lost: a shorter window (a video still talks inside
any window); matching her voice (no speaker model on the device).

**2026-10-08 — Calmer, smaller eyes.** Owner: "calm down on the
animations ... make it smaller". Resting pair now fits 0.42 x 0.30 of
its box (was 0.66 x 0.47, the 2026-10-07 "quite big" ask, reversed;
tests/test_eyes_extras.py's full-screen bounds updated to the new rule).
Same looks at about half the energy: idle drift every 4-9 s (was 2-5),
flourishes every 15-30 s (was 5-11), a passing visitor every 1-2 min
(was 14-30 s), attentive/listening growth 1.08/1.06 (was 1.2/1.12),
nods every 3-7 s and half as deep, speaking bob ~half amplitude and
slower, perk hop 10 (was 18) over 800 ms. Lost: removing flourishes and
visitors outright -- rarer keeps it alive without busy. Not checked in a
browser test here: no Chromium on this laptop, the JS tests skip.

**2026-10-08 — Medication module, Phase 1: eMAR seam and records.**
Owner's MVP brief: nurses give every dose, residents never
self-medicate, eMAR vendor unknown. One device per resident (owner).
Medication languages are Singlish (everyone's fallback), Cantonese,
Hokkien and Tamil (owner). New package `saathi/medication/`, its own
SQLite (`medication.sqlite3`; the mock eMAR in `mock_emar.sqlite3`) --
not the identity store, so `IdentityStore` is untouched. Lost: tables in
`identity/store.py`, which would put a ward's clinical data in one
resident's identity file. Choices:
- `EMARAdapter` is the owner's three calls plus `list_residents()`:
  the overdue check must walk every resident, and the three can't name
  them.
- `purpose` is a sentence per language, not one `purpose_plain_text`
  that a model translates (an inexact translation is an invented
  medical claim). Missing in both her languages -> the nurse explains.
- Two locks on the eMAR: every adapter's `record_event` refuses an
  event that isn't CONFIRMED with a nurse named, and `writer.py` is the
  only caller (a test greps the package). The eMAR is written before the
  local event is marked confirmed, so a failed write leaves it PENDING.
- The audit table and resolved events can't be updated or deleted
  (SQLite triggers), not just "aren't".
- `CSVImportAdapter` can't write into a vendor system: confirmed events
  go to `emar_outbox.csv` for staff to enter. An import with any bad row
  is refused whole, with line numbers; a purpose sentence that states a
  dose or an instruction is a bad row (`guard.py`).
- An accepted import makes the CSV live; otherwise the mock;
  `SAATHI_EMAR=mock|csv` forces one.
- Device assignment is append-only, latest wins (`saathi meds assign`).

**2026-10-08 — Test resident Lee Kim Tan (bed 6).** Owner asked for a
complete test profile with his son on +6589614304 -- already saved as
"Udhi" (the paired family member, "Udhi (son)") but with no relation, so
"call my son" couldn't find him; now saved with relation "son".
`saathi test-resident --by <who>` seeds the mock ward (Lee is the 11th
resident: ten daily doses, adding Allopurinol and Tamsulosin to the
seed), writes twelve profile rules to this device's identity file, each
sourced to one admission episode, and assigns the device to bed 6.
Idempotent. Preferred language Hokkien (fallback Singlish): realistic
for the name, and it exercises the fallback while Hokkien has no voice.
No medicine, dose or condition is in the identity file; one rule says
the nurses give his medicines and the nurse explains. Found on the way:
guard.py read "Teresa Teng" as "ten g" -- number words now need a space
before a unit.

**2026-10-08 — Renamed to Kaki (what she hears, says and sees).**
Owner's call. Changed: the wake word (`audio/wake.py`), the name the
router and the name-stripping take off a command, the tiny.en prompt
and the media-command prompts, the persona's first sentence ("Your name
is Kaki." -- the model had never been told a name, so "what's your
name?" got an invented one), the memory wording ("Kaki replied",
"She corrected Kaki"), the screen's title, settings and captions, the
family app, push titles, CLI messages. Not changed: the package, the
`saathi` command, `SAATHI_*` variables, `~/.saathi`, the repo -- code
names nobody hears; renaming them breaks every script and env for
nothing she notices. Measured before choosing the matcher: tiny.en with
prompt "Kaki" spelled it exactly "kaki" in 102/126 synthetic clips
(Google Neural2/WaveNet en-IN/GB/US/AU at 1.0x and 1.15x, Piper amy);
the misses were the name dropped before "what time is it?", which the
0.55 s early check heard in 14/14. "Kaki" is everyday Singlish, so a
name right after "my", "kopi", "mahjong" etc. is not a call, and the
similarity line rose 0.82 -> 0.88 ("kai" 0.86 out; "khaki" 0.89 in).
Cloud respellings for a name-only turn ("cocky", "kacky"...) are
guesses until the logs show real ones. The wake tests' Saathi-specific
misspellings (Sothai, Sati, Saudi) were replaced with Kaki ones -- the
behaviour they pin is unchanged. SPEC.md still says Saathi (TODO.md).

**2026-10-08 — The eyes' glow, ~12x cheaper per frame.** Owner: low
frame rate when the eyes move. Measured in headless Firefox at this
laptop's 3840x2230/dpr 2 (software raster, so absolute numbers are
high; the ratio is the point): 101-139 ms a frame, nearly all of it the
full-resolution `shadowBlur`. Now 8-12 ms: the glow is blurred on a
quarter-size canvas behind the face, stretched by CSS (the compositor
scales it), with only the shadow drawn there (an offset trick, so no
blocky low-res copy of the eye shows at its rim); the eyes are drawn
straight onto the face canvas, no offscreen copy; and the redraw box is
the pair's rectangle, not a square of its width. Pixel output compared
old vs new: the same eyes and glow. No browser test here (no Chromium).

**2026-10-09 — English and Mandarin only; Hindi and Bengali removed.**
Product owner's decision. `SUPPORTED_LANGUAGES` is now those two, so the
Ctrl+L panel, `set_language`'s enum, detection and every voice table
follow from it. A stored "hindi"/"bengali" preference is ignored like
any unsupported one (falls back to the last language, then English).
`script_language` counts only Han and *Latin* letters: Devanagari or
other scripts no longer vote, so the English voice is never handed them.
Lost: keeping them as extras that cost nothing — each extra language is
one more thing a bad detection can switch her into. Kept: `call/match.py`'s
Devanagari/Bengali transliteration (a contact's name can still come back
from the transcriber in those scripts) and `call/phone.py`'s
`LANGUAGE_COUNTRY` (now unreachable, harmless).

**2026-10-09 — No AI noise suppression (RNNoise) in front of the mic;
measured, it made things worse.** Offline A/B, the v1.10 LADSPA plugin
(VAD gate off) vs raw: 18 phrases (12 English, 6 Mandarin, Chirp3-HD
voices) mixed with fan hum, pink noise and TV-style speech at 10/5/0 dB
SNR, transcribed by `gpt-transcribe` and the `tiny.en` wake listener.
Word/char error on the request (name excluded), English raw vs RNNoise:
steady noise 1.5–7.6% vs 1.5–12.1% (a wash; the cloud model already
copes), TV speech at 5 dB 21% vs 39%, at 0 dB 59% vs 89%. Mandarin was
mixed, no clear gain. Wake word, English, over all noisy clips: 61/108
raw vs 43/108 with RNNoise — its artefacts cost a missed "Kaki" in about
one clip in six. Lost: wiring it in as a PipeWire filter (a
`module-ladspa-source` after the echo canceller). Caveats: synthetic
speech, one run, small sample; a real-room recording could differ.
The problem it can't touch either way is TV *speech*, which a denoiser
keeps by design — that needs a beamforming mic array, not software.
Harness: kept out of the repo (scratch), described here so it can be
rebuilt.

**2026-10-09 — A wake needs two models to agree: tiny.en hears "Kaki",
then the cloud transcript must contain the name, or the turn ends
silently.** Live: two people chatting across the table were answered
again and again; the name was never said. Measured: tiny.en primed with
"Kaki" turned 4–8 of 90 everyday sentences into the name ("Cookie or
cake?" -> "Kaki or Kaki?", "Wait ah..." -> "Kaki Kaki Kaki"). The cloud
transcriber, hinted with "Kaki." (`NAME_STT_PROMPT`) on wake turns only,
put the name in 10/12 real requests and in 0/30 ordinary sentences.
`mentions_name` accepts it anywhere (lyrics come first while music
plays) and accepts the cloud's respellings (tacky, packy, kaggy, ...).
To make that possible the name's own audio now leads the turn on the
early-wake path (screen/server.py); before, it was dropped, along with
any words inside it ("Kaki ca|ll Udhi" reached the cloud as "ll Udhi").
Lost, all measured: rejecting transcripts that repeat the name (7/90
still false); dropping the 0.55 s early check (2/90, but the name heard
11/18 instead of 15/18); no prompt for tiny.en (0/90 false, but the name
heard 7/18); RNNoise in front (worse). Cost: a real "Kaki" is missed
when the cloud misspells it past the list -- 2 in 12 English requests in
the test. Unmeasured: Mandarin, where the cloud wrote the name 卡奇 or
not at all ("开启", "咳嗽", 2026-10-09 Chirp clips, no hint); a missed
Mandarin wake is likelier than an English one until measured with a
real voice.

**2026-10-09 — Twilio TURN as a second relay option
(`SAATHI_TURN_TWILIO=on`).** Campus wifi (NUS_STU) failed every host and
STUN pair, so calls stuck on "Connecting…". The Twilio keys calling
already has can mint TURN credentials (Network Traversal Service,
verified live), so no new account is needed. Per-GB billing, so opt-in;
Cloudflare wins when both are set. See call/ice.py.

**2026-10-09 — Her language is the one chosen, never detected; the
transcriber is told it (`language=en|zh`).** Owner: "if I select
English I'm only speaking English; if Mandarin then Mandarin." Live,
Groq's whisper-large-v3-turbo guessing per utterance put Spanish, French
and Hindi in the captions. `STT_LANGUAGE_CODES` (voice/language.py) is
passed on every STT call, primary and fallback; the per-turn detection
block in cascade.py is gone. Her language changes only through Ctrl+L or
`set_language` ("speak to me in Mandarin"). Lost: auto-switching when
she changes language mid-conversation -- it switched on noise.

**2026-10-09 — The "Kaki." STT hint on wake turns is withdrawn, the same
day.** It was measured on gpt-transcribe, but the live transcriber is
Groq's Whisper (provider.py's split), which answered unclear speech with
the hint itself: "Kaki." alone read as her calling, opened "listening for
the rest", and the friend's next sentence was answered. Without it,
`mentions_name` relies on the cloud's own spelling plus the respelling
list. Lesson recorded: measure on the transcriber that is actually live.

**2026-10-09 — The face moves at under half the speed.** Owner: calmer,
for an old person to look at. EASE_RATE 12 -> 5 (blinks keep a quicker
BLINK_RATE 9), slower tilt/brightness/sparkle, idle drift 8-14 s,
flourishes 45-90 s, blinks every 3-7 s, breathing 6.5 s, nods every
6-12 s and slower, a smaller perk, a sway instead of the confused/laugh
shake, slower visitors. Browser tests (tests/test_eyes_*.py) could not be
run on this laptop: snap Chromium can't read /tmp and hangs headless.
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

**2026-10-08 — `pysilero-vad` is imported when the first detector is
built, and an RMS energy gate stands in when it can't be.** Engine step
"vad-flac" of the Chaquopy port: there is no Android wheel for
`pysilero-vad`, and a module-level import would have taken
`audio/vad.py`, and with it `cascade.py`, down at import time. The
import now happens inside `VoiceActivityDetector.__init__` (through
`_silero_class()`), so `monkeypatch.setitem(sys.modules,
"pysilero_vad", None)` in a test produces exactly the phone's state;
a module-level `try/except` would have been simpler but untestable
without reloading the module, and its constants would still have
needed a fallback. `CHUNK_SAMPLES`/`CHUNK_BYTES` are written down as
512/1024 instead of read off the class, and the loader raises if a
future `pysilero-vad` disagrees — a silent chunk-size change would
feed the model the wrong window. The fallback is a per-chunk RMS gate
with the chunk's DC offset removed, "probability" scaled so that
`ENERGY_GATE_RMS` (1000, about -30 dBFS) lands on the default 0.5
threshold; the same `SpeechStartDetector` debounce sits on top, so
three consecutive loud chunks are still the definition of "she's
talking" on either gate. 1000 clears the echo-cancelled source's
measured silence (RMS 1-750, 2026-09-24) and sits well under the
`known_sentence.wav` fixture's spoken chunks (median ~1650, runs of
16+ over 1000). What lost: `webrtcvad` (a C extension, the same
no-wheel problem), and a numpy spectral gate (more to get wrong and
nothing to tune it against until a phone has been in a room). The
gate in use is logged once per process, at `INFO` on both paths —
on the phone the energy gate is the expected configuration, not a
fault. Linux with `pysilero-vad` installed behaves exactly as before;
`VoiceActivityDetector.gate` says which one a process got.

**2026-10-08 — The STT upload is chosen per call: FLAC through
`soundfile` where it imports, a stdlib WAV where it doesn't.**
`cascade.py`'s `import soundfile as sf` is guarded (`sf = None` on
`ImportError`, there is no libsndfile wheel for Chaquopy), and
`end_turn()` calls `_encode_for_stt(pcm) -> (filename, bytes)` and
uploads under the returned name, because the endpoint reads the
container off the extension and the two must not be able to drift
apart at the call site. `_pcm_to_flac_bytes` keeps its name and
signature as the FLAC leg beside a new `_pcm_to_wav_bytes`, so the
round-trip test written against it still runs unchanged; folding it
into the chooser would have meant rewriting a passing test for no
behaviour change. Dropping FLAC altogether for one code path was
considered and rejected: the ~11% median time-to-response win
(docs/completed/latency-investigation.md) is real on the Pi and the
laptop and costs the phone nothing. The absent-soundfile test sets
`cascade_module.sf = None` rather than reloading the module with
`sys.modules` patched: that is the exact state the guarded import
leaves behind, and a reload would invalidate the class objects every
other test in the file holds.

**2026-10-08 — The AI service is reached without its SDK where the SDK
is absent, and `SAATHI_AI_CLIENT=rest` makes a laptop do the same.**
Engine step "rest-ai" of the Chaquopy port: `openai` and `groq` both
sit on `pydantic-core`, which has no Android wheel, so
`voice/engine/rest_client.py` is a `RestChatClient(base_url, api_key)`
over `urllib` that exposes exactly the attribute paths the engine
reads -- `chat.completions.create(...)` giving `.choices[0].message
.content`/`.tool_calls[i].id/.type/.function.name/.arguments` and
`.usage.prompt_tokens/.completion_tokens`, and
`audio.transcriptions.create(model, file=(name, bytes),
response_format)` giving `.text`/`.language` -- as frozen dataclasses,
with a hand-built multipart body for the upload, bearer auth, a 30 s
timeout, and one `RestClientError` whose message has the key scrubbed
out (the 401 test's server echoes the header back; the exception is
raised `from None` so a traceback can't print the body either).
`tools=None` is omitted from the request rather than sent as `null`:
the cascade passes `schemas or None` and the follow-up call passes
nothing, and both mean the same thing. `provider.py` tries the SDK
first and falls back to REST on `ImportError`, so a Linux install with
the SDK is byte-for-byte unchanged; `SAATHI_AI_CLIENT=rest` forces
REST and `sdk` forces the SDK (loudly, if missing), and a bad value is
reported by `missing_key()` the way a bad `SAATHI_AI_PROVIDER` already
is. Two things the SDK path had are carried over so a comparison is
fair: the IPv4 bind of the local side (the 2026-09-25 network finding)
through a custom `HTTPSHandler`, and a CA bundle -- the platform's,
falling back to `certifi`'s only when the platform store is empty,
which is Android's case. What lost: `requests` (on the phone, but
nothing over `urllib` for two POSTs), an `aiohttp` client (the cascade
is synchronous in an executor; an async client is a loop hop per call),
vendoring a pure-Python pydantic to keep the SDKs. Two module-level
`from groq import Groq` lines went with it: `cascade.py`'s was an
annotation only, and `reflect.py`'s now happens inside
`_default_client()`, which still builds `Groq(api_key=...)` exactly as
before where the SDK imports and `RestChatClient` where it doesn't or
`SAATHI_AI_CLIENT=rest` is set -- `reflect()` stays Groq-only because
its model name is Groq's. `smoke.py`'s key checks still import the SDKs
lazily (`client.models.list()` is not part of the surface the task
fixed); smoke doesn't run on the phone. Proven on Linux by
`tests/test_rest_client.py`: an aiohttp fake of both routes, and a real
`CascadeSession` turn over the REST client, tool call and follow-up
included.

**2026-10-08 — Chirp and Neural2 reach Google over plain REST where the
client library can't be installed, behind the same two ids.** Engine
step "google-rest" of the Chaquopy port: `google-cloud-texttospeech`
sits on `grpcio`, which has no Android wheel; `google-auth` is pure
Python and is what mints the token, and the REST `text:synthesize`
answers the same voice names with the same LINEAR16 WAV. So
`voice/tts/google_rest.py` holds `GoogleRestChirp3HDBackend` and
`GoogleRestNeural2WaveNetBackend`, each a subclass of its client-library
class with two things swapped -- the library probe (`_importable()`, a
new one-line hook in `google_backend.py` so the credentials check and
the cooldown are inherited, not copied) and the synthesis call (one
`urllib` POST per sentence through `voice/engine/rest_client.py`'s
opener, so the phone gets the same IPv4 bind and `certifi` fallback the
AI client already has). Voice tables, `chirp3_hd_voice_name()`,
`pcm_to_wav()`, the regional endpoint, the deadline, the cooldown and
`_guarded()`'s silence-not-raise rule are all imported. `registry.py`
decides per process through `google_tts_implementation()` -- the client
library where it imports, REST where only `google-auth` does, the client
classes (with their "optional dependency group" reason) with neither --
using the same two probes the backends' `available()` use, so the
choice and the panel's reason can't disagree; it logs the choice once,
the way `audio/vad.py` announces its gate. What lost. Separate
`google-rest-*` ids with an honest "per-sentence" display name for
Chirp (REST has no bidirectional stream; `stream_pcm()` keeps its
contract but yields the rendered sentence as one chunk): they would
have made the phone a different voice in `voices.py`, the stored
preference and the settings panel for a difference she cannot hear, and
the task's rule was no change to what she hears. `google.auth.default()`
in place of `from_service_account_file(path, scopes=[cloud-platform])`:
it would also honour a developer's `gcloud` login, and the three Google
identities `android/README.md` keeps apart must stay apart on the
laptop too. A whole-call deadline: `_REQUEST_TIMEOUT_S` is urllib's
per-socket-operation timeout here, read off `google_backend` at call
time so there is one number for both transports; a watchdog thread per
sentence was more machinery than a stall on Wi-Fi (which stalls, not
trickles) justifies. `find_spec()` in the registry: cheaper than the
import probe, but a second opinion on "installed" that could disagree
with `available()`'s. Making `rest_client._build_opener` public or
moving it to a shared `saathi/net.py`: either meant editing an earlier
step's module and its test for one import; the private import carries a
comment and the shared module is the thing to make when a third caller
appears. Proven on Linux by `tests/test_tts_google_rest.py`: an aiohttp
fake of `text:synthesize` (request shape, one POST per sentence, lazy
per sentence, a 403 whose body quotes the header degrading to 100 ms of
silence with a cooldown reason that has the token scrubbed out, the
deadline, a reply without `audioContent`, a refused connection), the
token fetched once and reused across sentences and both backends and
refreshed only when `google-auth` says it expired, the loader's exact
call shape through fake `google.oauth2`/`google.auth` modules in
`sys.modules`, the registry's three states, and a real
`CascadeSession.say()` through the REST Chirp backend. Credentials are
faked at the `ServiceAccountToken(credentials=, transport=)` seam; no
key file is ever read. Not verified here: a live call -- no credentials
and no network to Google from this machine -- and the Chaquopy `pip`
list (`google-auth`, `requests`, `cryptography`), which belongs to the
Gradle step.

**2026-10-08 — Remote-only audio mode, and the phone's own voice as the
TTS of last resort.** Engine step "remote-mode" of the Chaquopy port:
the engine runs inside the Android app at `127.0.0.1:8765`, so the
`/audio` client is the only microphone and speaker there is.
`SAATHI_AUDIO=remote` (a fourth knob in `config.py`, beside the port;
anything but `local`/`remote` is refused at load, not guessed) makes
`cli.build_runtime()` skip `DeviceManager`, PulseAudio and echo-cancel
entirely -- `audio/aec.py` and `audio/devices.py` are no longer even
imported on that path, since `pywebrtc-audio` and `pyudev` have no
wheels on the phone -- and build the `CascadeSession` on sink id
`"remote"` (not a device name: nothing local plays) with
`player=remote_audio.player` and `capture_source_id=None`, so
`screen/server.py` listens only through `RemoteAudio` and a press with
no client attached starts nothing and ends as a silent turn, never a
`parec`. `_build_calling(runtime, None)` makes calling report itself
unavailable (no cloudflared on a phone, no echo-cancelled pair). The
option that lost: probing anyway and letting `pactl` fail -- it would
have landed on "no microphone found; running without the voice engine",
the checkpoint-1 path with no session, on a phone whose microphone is
on `/audio`. In remote mode `RemoteAudio`'s fallback is
`_no_local_playback` (drop the sentence, warn) rather than `paplay`,
which the phone hasn't got; it is reachable only between a client
leaving and the turn noticing. The seam runs backwards now too:
`RemoteAudio.synthesize(text, language, timeout)` sends `{"type":
"synthesize", "id", "text", "language"}` to the client and waits for
`{"type": "synthesized", "id"}` followed at once by one binary WAV
(`server.py` routes the text frame to `on_synthesized`, and `feed()`
hands the next binary frame to the waiting request instead of the mic
sink), or `{"type": "synthesized", "id", "error"}` with nothing after
it. A `synthesized` for a request already timed out still tags the WAV
that follows, which is eaten, not heard: without that an answer
arriving during the next hold would be transcribed as her. The wait is
bounded (10 s by default; a dead client is detached by the heartbeat
and releases it at once, as for a play), and `RemoteSynthesisError`
is the one failure -- no client, left, replaced, error, timeout.
`voice/tts/remote_backend.py`'s `RemoteTTSBackend` ("android-tts",
"Phone voice", local, $0) is the `TTSBackend` over it: `available()`
iff a client is attached; one WAV per sentence; a failed sentence is
100 ms of silence plus a warning, never a raise, because
`cascade._prefetch_next_chunk()` only enqueues on success and a raise
there hangs `say()` (the rule `google_backend._guarded()` already
records; proposal 6 in docs/completed/voice.md is still the real fix
and still out of scope). Why over the socket and not Chaquopy's Java
bridge: a backend calling `android.speech.tts` from Python would have
been the voice engine reaching into the shell, and would exist only on
the phone; this one is the same class on a laptop with a phone on
Wi-Fi and in the suite with a fake. `cascade._current_backend()` no
longer ends in `self._backends[DEFAULT_BACKEND_ID]`: after the
preferred backend and the Google Neural2 rule it tries Piper if present
and usable, then the first usable backend in the dict, and when nothing
can speak (boot on a phone, before the client attaches -- construction
preloads the current backend) hands back Piper if present else the
dict's first entry rather than raise; an empty dict is a
`RuntimeError`. `cli._remote_backends()` therefore puts the phone's
voice first and the registry's after, minus Piper where `piper-tts`
does not import: first so the "nothing yet" answer is the one backend
whose synthesis cannot hang the prefetch thread and that becomes
available the moment the phone connects; the stored preference (Chirp)
and an installed Piper still win over it whenever they can.
`PiperBackend` imports `piper` lazily now (`_piper_importable()`,
`TYPE_CHECKING` for the annotation) and `available()` says "piper-tts
is not installed" instead of the module raising at import and taking
the registry and the screen server down with it; `preload()` is a
no-op in that state so no daemon thread exists only to raise. The
registry still lists Piper on the phone, greyed out with that reason --
leaving it out would have made `DEFAULT_BACKEND_ID` name a key that may
not exist and told the panel nothing. What lost, besides: the phone's
voice in the settings panel (`_settings_message` builds from
`default_backends()`, which has no `RemoteAudio`; it is reached
automatically as the last resort, and offering it as a choice is a
later step if anyone asks for it); a `preload()` on the remote backend
(the shell initialises its engine on connect, where the cost belongs);
a BCP-47 tag in the request (the shell maps `english`/`chinese`/
`hindi`/`bengali` to a `Locale`, a lookup, not a decision). Proven on
Linux by `tests/test_cli.py` (remote mode builds no `DeviceManager`,
sink "remote", the remote player, android-tts first in the backends,
Piper left out when `piper` is unimportable, calling unavailable with
and without Twilio, the dropped-sentence fallback, the unknown-mode
refusal), `tests/test_audio_remote.py` (round trip beside mic frames,
timeout with the late answer eaten, no client, the error answer,
detach/replace release, a second `synthesized` before the first WAV),
`tests/test_tts_remote.py` (the backend's contract, and a real
`CascadeSession.say()` rendered by a fake phone and played back through
it), `tests/test_cascade.py` (fallback to the first usable backend
without Piper, Piper still preferred when usable, the nothing-can-speak
answer, the empty-dict error), `tests/test_screen_server.py` (the
`synthesized` route end to end, the error answer, a malformed one
dropped) and `tests/test_tts.py` (Piper's reason without raising). Not
verified here: a real phone's `TextToSpeech` output, and the shell's
side of the two frames, which is the Chaquopy step's.

**2026-10-08 — The entry point Chaquopy calls is `saathi/android.py`:
`start(config)` and `stop()`, module-level, and nothing at import.**
Engine step "entry-point" of the Chaquopy port. Chaquopy calls Python
by module and attribute from a Java thread that must get control back
(a service's `onCreate`/`onDestroy`), so the entry is two functions
and a module-level "what is running" -- no argparse, no console
script. What lost: `saathi.cli.main(["run"])` on a thread under a fake
argv. `main()` ends in `web.run_app()`, which owns the loop, installs
signal handlers (main thread only; Chaquopy's caller is not it),
prints, and returns only when the process is told to stop: no
`stop()`, no way to know the server was up before the shell pointed
its WebViews at it, and the keys would have had to be in the
environment before `main()` ran. `start()` still goes through the same
`cli.build_runtime()` and `screen/server.py`'s `build_app()` that
`saathi run` does; it is the thin thing around them a Java thread can
hold. The keys are a dict argument (`OPENAI_API_KEY`, `GROQ_API_KEY`,
`YOUTUBE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`, `TWILIO_*`),
not a file in the APK -- an APK is readable by anyone who has it --
and go into the environment, the one place the engine already looks,
so the engine is byte-for-byte what runs on Linux. Only those names
are accepted: an unknown one is a `ValueError` before anything starts
(a typo in the shell's constant is a build mistake to find on the
first run, and a dict that could set `SAATHI_AUDIO` would be the shell
choosing the engine's mode -- `start()` sets `SAATHI_AUDIO=remote` and
`SAATHI_AI_CLIENT=rest` itself); an empty or null value is "not set";
names the shell did not send are left as the process had them (a
laptop exercising this path keeps its own keys). The Google credential
is JSON in the shell's hands but a path to every Google client, so it
is written to `<data_dir>/gcp.json` with mode 0600 (`os.open` with
0600 and `fchmod`, so a file a previous run left world-readable is
tightened, not trusted) and `GOOGLE_APPLICATION_CREDENTIALS` points at
it; the JSON itself never enters the environment. JSON that does not
parse is noted and not written -- the Google voices then report
themselves unavailable with a plain reason and the phone's own voice
carries on -- rather than refusing to start; an empty credential
removes a stale file, so a key taken out of the shell's settings is
gone. The socket is bound on the caller's thread before anything else
(`web.SockSite` over it), so "the port is taken" is a `RuntimeError`
with the number in it raised by `start()`, not a traceback on another
thread; port 0 is honoured and the bound port returned, which is what
the tests use. Readiness is `GET /` answering 200 over a real socket,
through an opener with no proxy whatever the environment says (this is
localhost), for up to ten seconds; the runner's own "started" would
not have proven what the WebView is about to ask. The runtime is built
on the server thread, not the caller's -- found by the first test run,
not by inspection: `IdentityStore`'s sqlite connection is bound to the
thread that opened it and `screen/server.py` reads the store on the
loop (settings on connect, the turn log, captions); under `saathi run`
the two are one main thread, and building on the caller's thread here
killed the first `/ws` connection with `ProgrammingError`. For the
same reason the store is closed on that thread, after the runner, and
calling's `shutdown()` runs there too (never built in remote mode;
the contract is still "stop what was started"). `stop()` signals the
loop, joins the thread (10 s, then a warning), closes the socket and
puts every variable `start()` set back the way it found it, so a
second `start()` with different keys starts clean; the runner's
`shutdown_timeout` is 3 s, not aiohttp's 60, because the shell closes
its sockets before it stops the engine and a service's `onDestroy`
must not hang a minute for the one that did not. A `start()` while
running is refused ("call stop() first"), not a silent restart:
restarting would hide a shell that leaks engines. A start that cannot
finish (port taken, the server thread dying, no answer in time) tears
everything down and raises; nothing is left running or set. Tests
(`tests/test_android_entry.py`) use `test_cli.py`'s fake
`CascadeSession` because the real one warms a voice at construction,
which on a Linux box with `piper-tts` means a voice model downloaded
into `~/.saathi` from inside a test; the one start without any AI key
runs the real `build_runtime()` end to end. Importing the module is
proven inert in a subprocess: no environment change, one thread,
`aiohttp`/`saathi.cli`/`saathi.screen.server` not even imported,
nothing written under a scratch `HOME`. Not verified here: what a
Chaquopy-wrapped `java.util.Map` does under `config["data_dir"]`
(`android/README.md`'s snippet builds a Python `dict` for that
reason), and the Gradle plugin, requirements and the service call
itself, which are the Chaquopy step's.

**2026-10-08 — The engine goes into the APK with Chaquopy 15.0.1 and
Python 3.12; the package is staged by a Gradle `Sync` task; five pip
packages and nothing else.** Android step 1 of the Chaquopy port. Why
Chaquopy: it is the one maintained way to put CPython in an APK that
also has a pip resolving Android wheels from its own index, so the
engine is the same package the suite tests, not a port. What lost:
BeeWare's Briefcase (a different app shape, the shell already exists),
python-for-android (builds a toolchain for an hour in CI), Termux
(a second app, and not installable from the store). The version: 15.0.1
is the first line with Python 3.12, the interpreter `uv` pins
(`requires-python >= 3.12`); its AGP range could not be checked --
chaquo.com was unreachable (proxy 403, DNS) from the machine this was
written on -- so the root build file says what to do if the first CI
build refuses AGP 8.5.2 (Chaquopy 16.x, one line). The DSL is Chaquopy
14+'s `chaquopy { defaultConfig { ... } }` block, not the
`android.defaultConfig.python { }` block the task text sketched: that
form was deprecated in 14.0 and is the one that would fail on 15; the
method names (`pip { install(...) }`, `extractPackages(...)`,
`sourceSets { getByName("main") { srcDir(...) } }`) are from the
documentation as remembered and are flagged in the README as what the
first build checks. How the package gets in: `syncSaathiPython`, a
`Sync` (a module deleted from the repo must leave the APK too) from
`../../saathi` into `app/build/saathi-python/saathi`, excluding
`__pycache__`, `*.pyc` and `audio/testdata` (the AEC bench's recordings,
megabytes the phone never plays); `app/build/saathi-python` is the
Python source set, because a source set is the parent of the package
and the repo root holds tests, docs and scripts. `preBuild` depends on
it, and so does every task named `generate*Python*`, by pattern: AGP
promises `preBuild` before its own tasks, not before a third-party
plugin's, and a Chaquopy update that renames a task still matches. What
lost: a symlink `app/src/main/python -> ../../saathi` (Git on Windows
and AGP's source scanning treat symlinks unevenly) and a wheel built by
`uv build` for Chaquopy's pip (a build inside a build, and its pip
takes wheels from its index, not from a path). `extractPackages("saathi")`
because `screen/server.py` serves the face from `Path(__file__).parent
/ "static"`, which must be a real directory whatever Chaquopy's rule
for data files is; a few megabytes unpacked once. ABIs `arm64-v8a` and
`x86_64` (Chaquopy refuses a build with no filter; 32-bit ABIs are
another interpreter each for devices this is not for). The pip list is
`aiohttp`, `numpy`, `requests`, `google-auth`, `cryptography`,
unpinned: the versions are what Chaquopy's index carries for cp312, and
pinning to `pyproject.toml`'s floors (`aiohttp>=3.10`, `numpy>=2.5.3`)
would fail the build if the index is behind them -- a known gap: the
phone may run older aiohttp/numpy than the laptop, and the suite does
not run under those versions. Everything else in `pyproject.toml` has
no Chaquopy wheel and is guarded at import by the earlier steps;
`tests/test_android_phone.py` proves the sum (below). Also in the
`settings.gradle.kts`: `https://chaquo.com/maven` in both repository
blocks, after Google and Central (where the plugin and runtime have
been published since Chaquopy 12), as the task asked and as Chaquopy's
documentation names; pip packages never come from there.

**2026-10-08 — `EmbeddedEngine` is an object with one worker thread;
Python starts there; a start with the engine's current keys is "ready",
with other keys a restart; the engine is stopped when the activity
finishes or the brain moves, not when the activity is recreated.**
One worker, not a pool and not the main thread: `Python.start()`
unpacks the standard library on a first launch and
`saathi.android.start()` imports numpy and aiohttp and waits for the
server -- seconds, and an ANR over her face if on the main thread; one
thread so a `stop()` queued after a `start()` runs after it, and two
activities (a renderer crash recreates one) cannot start the engine
twice. `runningKeys` is the engine's state on this side: equal keys
report ready at once, different keys stop and start again, so the
dialog's Save with new keys is a restart and a recreated activity is a
reconnect. The config crosses as a Python `dict` built through the
interpreter's builtins (`__setitem__` per entry), not a Kotlin `Map`
handed to `callAttr`: what Chaquopy's `java.util.Map` proxy does under
`dict(config)` and `config["data_dir"]` is not something the Linux
tests prove, and a dict is what they prove against (the README snippet
of the entry-point step said the same). What is logged: a Python
exception's type and message (a port, a variable *name*), and the dict
`start()` returns (host, port, url, notes) -- never a key value; the
worker catches `Throwable` as well as `PyException` so a missing native
library (`UnsatisfiedLinkError`) is a reported error and a dialog, not
a dead thread and a black face. What lost: a foreground service for the
engine (the engine is a daemon thread in Python in this process, and
`EngineService` already holds the process foreground while the face is
up; a second service is a second lifecycle for one process), and
`PyApplication` (starts the interpreter on the main thread at every
process start, including in remote mode, which never needs it).
Stopping on `isFinishing` only: a kiosk never finishes, a recreate must
not restart a ten-second engine, and Android kills the process anyway.

**2026-10-08 — The keys are one pasted `.env`, kept in
`EncryptedSharedPreferences` with a plain-file fallback; names shown,
values never; a blank value removes; `brainMode` defaults to the
phone.** `Settings.kt` gains `brainMode` (`"phone"` | `"remote"`;
anything else stored reads as phone, `Settings.brainModeOf`) and a
`Keys` store on its own file: the keys are what an APK must never
contain (it is readable by anyone who has it), so they live in the
app's private storage and in a file the keystore encrypts
(`androidx.security:security-crypto:1.1.0-alpha06`, `MasterKey`
AES256-GCM, `EncryptedSharedPreferences` AES256-SIV keys / AES256-GCM
values), and a backup or a copied `shared_prefs` directory holds
ciphertext. When the keystore throws at open -- it does, on some old
phones and after some restores -- the keys go to a plain private
preferences file with one warning: a phone that cannot think at all is
worse than one whose keys are as protected as its identity database
already is. A single entry that no longer decrypts reads as "not set"
(name logged, never the value) rather than crashing the dialog. One
multi-line box for the whole file, not nine fields: the keys arrive as
the laptop's `.env`, and nine fields are nine chances to paste into the
wrong one. `Keys.parseEnvText` is pure and tested on the JVM: `KEY=VALUE`
lines, a leading `export ` dropped, matching single or double quotes
stripped and the content otherwise verbatim (no escape processing: a
service-account JSON in single quotes must survive untouched), an
unquoted value cut at an inline ` #` comment (API keys have no spaces),
`#` lines and blank lines skipped, a name that is not `[A-Za-z_][A-Za-z0-9_]*`
ignored, later lines winning; `GOOGLE_APPLICATION_CREDENTIALS_JSON=` may
open a `{` the line does not close, and the following lines are taken
until the braces balance outside JSON strings (a backslash escapes), so
the file pasted as-is after the `=` is one value; a JSON that never
closes takes the rest of the text (one wrong value the engine then
notes as "not valid JSON", rather than a parser that guesses where it
ended). Every name found is returned; `Keys.known` / `putAll` keep the
nine the engine reads (`saathi/android.py` refuses any other, so the
shell never sends one) and a blank value removes that key. The dialog
lists which names the phone holds and never a value, and the required
rule is `Keys.canThink`: `OPENAI_API_KEY` or `GROQ_API_KEY`, the rest
optional. What lost: showing stored keys masked (a masked key cannot be
checked and the name is what a person needs), and refusing to run
without the keystore (above).

**2026-10-08 — The setup dialog: the brain choice above two pages
(Engine, Keys) under one Save; it opens on the page that is missing
something; in phone mode it no longer connects the links -- the
activity does, once the engine answers.** `SetupDialog.show` takes a
`Page`; `MainActivity.applyBrain()` opens it on Keys when the phone is
to think and has no AI key, on Engine when it is to use an address and
has none, and the hold opens it on Engine. Save stores what changed
(the address is required and validated for a remote engine, optional
but still validated for the phone's own, so a typo is never stored),
stores what was pasted (a toast names what was stored or removed --
names), the mode and the kiosk box, and calls back; only with a remote
engine does it still point the links at the stored address itself,
since with the engine on the phone there is nothing to connect to until
`EmbeddedEngine` reports ready, which the dialog cannot know.
`applyBrain()` is the one path for both modes and for every Save: it
stops the embedded engine when the brain moves out and starts it when
the brain moves in, disconnects the links first (a switch from an
address must stop talking to it now), and connects face and links to
`EmbeddedEngine.URL` or the stored address through one `useEngine()`.
The active address is a field of the activity, not `Settings.engineUrl`:
storing `127.0.0.1:8765` there would lose the typed address on every
switch and would be the placeholder mistake of the first draft again
(an address nothing typed). A failed start is a toast with the
exception's text and the dialog on the Keys page, so the person setting
up can paste again or move the brain; the loop is driven by their Save,
never by a timer. The dialog's title became "Setting Saathi up"
("Where is Saathi's engine?" is now one page's question). Tabs are two
buttons toggling two columns inside a `ScrollView` (a pasted credential
is long and the screen is a phone's in landscape); a `ViewPager` or a
second dialog lost as more code for the same two columns.

**2026-10-08 — `tests/test_android_phone.py` proves the APK's five pip
packages are enough: a subprocess with every phone-absent package made
unimportable, the real `CascadeSession`, `GET /` 200.** The earlier
steps proved each fallback alone (`test_vad`, `test_cascade`,
`test_tts`, `test_provider`, ...); this is the sum, the way the app
calls it (`saathi.android.start()` with an AI key, `SAATHI_DATA_DIR` as
the app's files directory), with `pysilero_vad`, `soundfile`, `piper`,
`onnxruntime`, `pyudev`, `pywebrtc_audio`, `grpc`, `openai`, `groq`,
`pydantic_core`, `pydantic`, `google.cloud`, `faster_whisper`, `kokoro`
and `torch` absent at once, and a `HOME` of its own so a voice download
would show up as a file. An absent package is `sys.modules[name] =
None` -- exactly what the import system does for one that is not
installed: `import` raises `ModuleNotFoundError` and
`importlib.util.find_spec` returns None. The first draft of the probe
was a `meta_path` finder that raised for the blocked names, and it took
the server thread down: `yarl` (under aiohttp) calls
`find_spec("pydantic_core")` at import and expects None, not an
exception. A probe that is stricter than a real phone proves nothing
about the phone; this one matches it. Subprocess, not in-process, for
the same reason as the import-side-effect test: the suite's process has
every package loaded already and `sys.modules` cannot be unloaded
honestly. The Kotlin side was compiled on this machine's JVM (Kotlin
2.0.21, warnings as errors) against stubs of the API 26-34 classes the
tree uses plus Chaquopy's and security-crypto's documented shapes, with
all 103 JUnit tests green; what that cannot check -- the Gradle script,
the real Chaquopy signatures, the AGP range, the wheels' existence for
cp312 -- the README lists for the first CI build.

**2026-10-08 — The Keys page is one masked field per key, a plain box
for the Google JSON, "Clear" per key, and "Paste .env from clipboard"
that fills the fields by name; an empty field changes nothing; Save
stays open when it refuses; the dialog's pages are Brain and Keys.**
This replaces the one-box Keys page of the Chaquopy step, and the
reason is what that box did in a living room: it showed every key in
clear for as long as the file sat in it, a person with one new key
re-pasted the whole file to be sure which name it landed under, and
nothing on the page said, per key, what the phone already held. Now
`SetupDialog.kt` builds, for each name in `Keys.NAMES`, a label that
says only `NAME: set` or `NAME: missing`, a field with
`TYPE_TEXT_VARIATION_PASSWORD` (dots on the screen, no keyboard
suggestions) and a Clear button; `GOOGLE_APPLICATION_CREDENTIALS_JSON`
gets the one plain multi-line field, because a masked multi-line box
cannot be checked for a missed brace and the file is pasted whole,
once. The `.env` file can still be pasted once: the button reads the
clipboard (`ClipboardManager.primaryClip`, text coerced; Android 10+
hands it only to the focused app, which a press on the dialog's own
button is) through the same `Keys.parseEnvText` and fills the fields
by name, a blank `NAME=` clearing that key as it did before, and a
toast names the keys it touched. What lost: a box on the page to
paste the file into as well as the button (a second place to paste the
same thing); masking the JSON box (above); and a layout file for the
page (nine copies of a row that is a loop in code). The model of what
Save will do is `KeyEdits`, pure Kotlin tested on the JVM, and the
widgets only show it: the dialog opens with every field empty and a
stored value is never shown back, so an empty field has to mean "as it
was" -- the first draft read the nine fields at Save and treated an
empty one as "remove", which would have wiped every key on the first
Save of a dialog opened to tick the kiosk box -- so a typed value
replaces, Clear removes a stored key and forgets what was typed, the
last action on a name wins, and `changes()` comes in the engine's order
with a blank meaning remove, exactly `Keys.putAll`'s shape, so Save is
one call and the toast names what was stored and what was removed.
Save is wired to the positive button after `show()` rather than through
the builder: a builder listener has the dialog dismissed whatever it
decided, and a Save refused for an address off the home network, or
for a phone that is to think and would hold no AI key (`KeyEdits.canThink`,
the same rule `MainActivity.applyBrain()` applies -- it used to let the
dialog close and reopen it at once with the pasted keys gone), now
shows the page that needs attention, says why, and keeps everything
typed. The restart of the engine with new keys stays the activity's:
Save calls back into `applyBrain()`, which hands `Settings.keys.all()`
to `EmbeddedEngine.start`, and that restarts the engine only when the
keys differ from the running ones; the dialog calling `EmbeddedEngine`
itself lost, since the links can only be connected once the engine
answers, which the activity's callback is the one place to know. The
pages are now Brain (the choice, with the address and Test indented
under "on another computer") and Keys; `SetupDialog.Page.ADDRESS`
became `Page.BRAIN`, the "Engine" tab "Brain", and
`setup_keys_hint`/`setup_keys_present`/`setup_keys_none`/`setup_page_engine`
left the strings with the box. Compiled and run on the JVM as before
(the stubs gained `ClipboardManager`, `ClipData`, `TextWatcher` and the
password input type, written from the platform's documentation); what
that cannot check is that a masked `EditText` takes a long paste and
that the clipboard read succeeds from the dialog on Android 10+ --
both for the first run on a device.

**2026-10-08 — The TTS bridge: the shell answers the engine's
`synthesize` with `android.speech.tts.TextToSpeech`, as a WAV sent
back over `/audio`, not as speech played on the phone.** The engine
inside the APK has no Piper (`onnxruntime` has no Chaquopy wheel) and
Chirp needs a key file and a network, so `audio/remote.py`'s
`synthesize()` asks the attached client for each sentence; this is the
client's half, in `OkHttpAudioLink.kt` (`PhoneVoice`). It renders with
`synthesizeToFile` into a file of its own under the app's cache
directory (`cache/tts/`, `File.createTempFile`, never a name derived
from the engine's id: `tts-1` comes round again with every engine
restart), reads it back when the `UtteranceProgressListener` says it
is done, sends `{"type":"synthesized","id"}` and the WAV as the very
next frame, and deletes the file in every outcome. `speak()` to the
speaker lost: the WAV goes up so the engine plays it through the one
`play`/`played`/`stop` path every backend's sentence takes, and
barge-in, the face's "speaking" and the turn's clock stay the engine's.
Bundling a TTS model lost as tens of megabytes and a second speech
stack for a voice used only when the better ones are not; calling the
platform from Python through Chaquopy's bridge lost because that is
the voice engine reaching into the shell, and it would exist only on
the phone (`remote.py` and `remote_backend.py` say the same from their
side). One `TextToSpeech` per connected span, made in `connect()` and
shut down in `disconnect()`, kept across reconnects: its first start
is a second or two and belongs at connect time, not on her first
sentence; sentences asked before `onInit` wait for it in order, and an
`onInit` of ERROR (a phone with no engine at all -- it can even fire
inside the constructor, which is why `engine` is the last property
initialised) refuses each with an error. The engine's language keys
map to `Locale.US`, `Locale.SIMPLIFIED_CHINESE`, hi-IN and bn-IN
(`localeFor`, built with `forLanguageTag` because the two-argument
`Locale` constructor is deprecated from JDK 19 -- not on Android -- and
the value is the same, which the JUnit test pins against the
constructor); a key this build does not know is answered with an error,
not a guess at a locale, as is a language whose voice is not installed
(`setLanguage` < 0), an engine that refuses the sentence, a WAV that is
not 16-bit PCM (checked with `WavHeader.parse`, so the failure is named
here rather than silent when the engine plays it back), and an
utterance the engine neither finished nor failed within 15 s -- longer
than the engine's own 10 s wait, so the shell never gives up first and
its late error is eaten there as stale. Every send on the socket -- mic
frames, `played`, `synthesized` and its WAV -- now goes through one
`sendLock`, taken before the state lock and never after it: the text
frame tags the *next* binary frame as the sentence, and a mic frame
from the capture thread landing between the two would have been played
as her reply and the WAV transcribed as her speech. A reply is sent
only to the socket that asked (`socketOf`): the engine fails a replaced
socket's requests when the new one attaches, and an answer with a stale
id would only be eaten. The engine side was checked, not changed:
`server.py` routes `{"type":"synthesized","id","error"}` to
`RemoteAudio.on_synthesized`, which fails that request, `synthesize()`
raises `RemoteSynthesisError`, and `RemoteTTSBackend` plays a short
silence -- `test_the_clients_error_answer_fails_the_request_and_no_wav_follows`
and `test_a_synthesized_error_is_routed_and_a_bad_one_is_dropped` pin
it. `AudioLink` did not change for this: the voice is the
implementation's, nothing on the screen takes part, and the engine
plays the WAV back through the existing `onPlay`. Checked on the JVM as
before: the whole `android/` tree compiled with Kotlin 2.0.21, warnings
as errors, against the stub set plus `TextToSpeech` and
`UtteranceProgressListener` written from their documentation (the
listener's `String` parameters are platform types, so the overrides
take `String?`; the deprecated abstract `onError(String)` is overridden
and marked deprecated, the two-argument form beside it), and all 118
JUnit tests in 17 classes green -- also the bridge's own files alone,
so an unfinished edit in a file another step was changing at the same
time could not have hidden a fault here. Not checkable from here: which
sample rate and format a given phone's engine writes (the header check
covers the format), how long its first synthesis takes against the
engine's 10 s wait, and that `cacheDir` is writable for the engine's
process through the file descriptor `synthesizeToFile` hands it.

**2026-10-08 — The APK is built on GitHub Actions
(`.github/workflows/android.yml`): `gradle assembleDebug` under
setup-gradle 8.7 with setup-java 17, setup-python 3.12 and
setup-android, the APK kept 14 days; a second job runs ci.yml's engine
suite step for step.** Android step 2 of the Chaquopy port, "ci". The
machines this project is written on have no Android SDK and Google's
hosts are blocked from them, so the only builder is a runner, and the
artifact page is where the phone gets its APK from. The engine job is a
copy of ci.yml's `test` job, matrix and all (x86 and arm64: SPEC.md's
"CI on x86 and arm64", and CLAUDE.md's "done" is both), not a `needs:`
on the APK job or a reuse through `workflow_call`: the APK should come
out as early as it can, the run is red if either job is, and a change
to ci.yml that reaches for a reusable workflow was out of scope ("do not
edit ci.yml"); the copy is pinned to the original by
`tests/test_workflows.py`, which fails when a `run:` step of ci.yml is
missing from android.yml or runs out of order -- with a regular
expression over single-line `run:` values, not a YAML parser, because
PyYAML is not a dependency and one test is no reason to add one (the
test's docstring says what that does not catch: a drifted `uses:` or
`with:`). What else was decided: `actions/setup-python` 3.12 is in the
APK job although the runner's own `python3` is 3.12 today, because
Chaquopy's build Python compiles the engine to `.pyc` and must be the
same minor version as the 3.12 in the APK, and a runner image's default
Python moves; `cache-read-only: false` on setup-gradle, since by default
only the default branch writes the Gradle cache and this workflow runs
on `android-app`, where every push would otherwise re-download the
runtime and the wheels; `validate-wrappers: false`, since the wrapper
jar is not in git (android/.gitignore) and there is nothing to validate;
a `concurrency` group that cancels the previous run of the same ref (a
second push wants the newer APK, not two); `timeout-minutes: 45` (a hung
Chaquopy pip must not run for six hours); `--stacktrace` on the one
Gradle call, because the first build is expected to turn something up
and a second round trip to see where is the expensive part. What lost:
`testDebugUnitTest` in the job -- the JUnit tests ran green on a JVM
against stubs, and whether they run under the real `android.jar` (its
"not mocked" stubs, `unitTests.isReturnDefaultValues`) is a question for
after the first APK has built, not one to make the first run red over;
and a path filter on `android/**`, because the APK carries `saathi/`
and a Python-only push changes it. Verified, with Chaquopy's source
(github.com/chaquo/chaquopy, tag 15.0.1; chaquo.com itself is
unreachable from here): the plugin checks only a minimum AGP (7.0.0,
`Common.MIN_AGP_VERSION`; `checkAgpVersion` has no maximum, and the
current docs list 15.0 as 7.0-8.5), so AGP 8.5.2 passes; Python 3.12.1
is in `PYTHON_VERSIONS`, with `arm64-v8a` and `x86_64` the ABIs for it;
`findBuildPython` tries `python3.12`, then `python3`, then `python`,
and makes a venv `--without-pip` to run its own bundled pip, so the
runner's Python needs no pip of its own; the DSL names in
`app/build.gradle.kts` (`chaquopy { defaultConfig { version; pip {
install(...) }; extractPackages(...) }; sourceSets { getByName("main") {
srcDir(...) } } }`) are the ones `PythonDsl.kt` declares; and a
build Python of the wrong minor version is a warning and no `.pyc`,
not a failure. Not verified, and what the first run will say: whether
Chaquopy's package index carries cp312 wheels for `aiohttp` (recipe
3.9.1 at that tag), `numpy` (1.26.2) and `cryptography` (3.4.8) -- the
recipes exist, the built wheels are on chaquo.com -- and which SDK root
`setup-android` chooses on the runner (either way it exports
`ANDROID_HOME`, which is all AGP needs). The workflow's YAML was parsed
here; its actions' input names were checked against their action.yml
files where memory was not enough.

**2026-10-08 — `android:testOnly` on the debug build is a Gradle
property, `saathi.testOnly` (default true), through a manifest
placeholder; the workflow builds a push with it false and a manual run
with it true by request.** The task asked for a README section that
ends "install (allow unknown sources)" -- the APK tapped on the phone
-- and the debug manifest overlay of the kiosk step says
`android:testOnly="true"` so that `dpm remove-active-admin` can take a
Device Owner off a tablet. Those two cannot both hold for one APK:
Android's package installer refuses a testOnly APK outright
(`INSTALL_FAILED_TEST_ONLY`; only `pm install -t`, which is `adb
install -t`, sets the flag that allows it), so the task as written
could not be done with that manifest, and rather than narrow it
silently or drop the dpm path the earlier step chose deliberately, the
attribute became `android:testOnly="${saathiTestOnly}"` filled from
`providers.gradleProperty("saathi.testOnly").getOrElse("true")`,
accepted only as the words true or false. Local builds are unchanged
(testOnly; `adb install -t`; dpm removal works). The workflow passes
`-Psaathi.testOnly=false` on a push, since that APK is fetched onto a
phone with no adb, and a `workflow_dispatch` input `test_only` builds
the testOnly one again, uploaded as `saathi-debug-apk-testonly` so the
two cannot be confused on the Artifacts list; the README's tablet
section says to use that one if the owner is ever to be removed, and
that an owner set from the pushed APK stays until a factory reset. What
lost: a second build type or flavor for one attribute (every Chaquopy
packaging step twice, for every build); dropping testOnly altogether
(the dpm path was added in review for a reason); and AGP's own
`android.injected.testOnly` property, which only adds the attribute
(it is how Android Studio marks a deploy) and cannot take one out of
the overlay. Noted for later, not done: a Device Owner can clear
itself with `DevicePolicyManager.clearDeviceOwnerApp()` from an "Exit
kiosk" that the dialog could offer on the owner tablet, which would
make testOnly unnecessary for removal; that is a `Kiosk.kt` change and
a semantics question (a five-second hold that un-owns a tablet) for a
step of its own.

**2026-10-08 — The debug signing key is `android/app/debug.keystore`,
in git, and the debug `signingConfig` points at it.** AGP signs debug
builds with `~/.android/debug.keystore`, which it generates per machine
when missing; a GitHub runner is a new machine every run, so every APK
from the Actions page would carry a new key, and Android refuses to
install over an app signed with a different key
(`INSTALL_FAILED_UPDATE_INCOMPATIBLE`) -- the only way on would have
been to uninstall first, which deletes the app's private files: her
identity database and the pasted API keys, on every update. One key in
the repository, used by every debug build wherever it is made, is what
lets "download the newest green run and install" be the whole
procedure. It is the conventional debug key (PKCS12, alias
`androiddebugkey`, store and key password `android`, CN=Android Debug,
RSA 2048, 10,000 days), generated here with the JDK's keytool; it is a
secret to nobody -- its whole content is the ability to install a debug
build over this one, on a phone one already holds -- and the Gradle
header says it must never sign a release. What lost: caching the
runner's keystore with `actions/cache` (evicted after a week unused,
and then the same uninstall), and a signing key in a repository secret
(a real secret for a debug build, and a fork or a local build then
signs differently again).

**2026-10-08 — The whole `android/` tree was compiled against the real
API-34 framework classes and the real Chaquopy runtime, as well as
against the stubs; the Chaquopy plugin was read from its published jar.**
Google's hosts are unreachable from here (no SDK, no androidx), but
Maven Central is, and two real things live there: Robolectric's
`android-all:14-robolectric-10818077`, the API 34 framework built from
AOSP, and Chaquopy's `chaquopy_java:15.0.1`, which carries `Python`,
`PyObject`, `PyException` and `AndroidPlatform`. Every main file and
every test compiled under Kotlin 2.0.21 with warnings as errors against
those two plus okhttp/okio/org.json, with stubs left only for androidx
(appcompat, activity, core, security-crypto), and the 118 JUnit tests
ran green -- so every `android.*` and `com.chaquo.*` signature the shell
calls is now checked against the real class, not a transcription of its
documentation. The stub pass stays beside it: android-all is compiled
with source-retention nullability annotations, so a wrong override
nullability passes there and fails only against the stubs, which carry
the documented one. Lost: dropping the stubs (above), and running AGP
(`com.android.tools.build` is on Google's Maven). The plugin itself
(`com.chaquo.python:gradle:15.0.1`, also on Maven Central) was read with
`javap` instead of from GitHub source: its AGP check is a minimum of
7.0.0 and no maximum, its Python table carries 3.12.1, its DSL is
`ChaquopyExtension.defaultConfig`/`sourceSets`,
`PythonExtension.version`/`pip`/`extractPackages`,
`PipExtension.install`, and its tasks are `<verb><Variant>Python<Noun>`
(the entry below). What no JVM check reaches is unchanged and recorded
in `android/README.md`: constants, resources, platform behaviour, and
whether Chaquopy's index has cp312 wheels of the five packages.

**2026-10-08 — `syncSaathiPython` is a dependency of every task with
`Python` in its name, not only the `generate*` ones.** The first pattern
(`startsWith("generate") && contains("Python")`) was written from the
guess that Chaquopy's tasks are all `generate<Variant>Python...`. Read
off the plugin jar's `TaskBuilder`: the one that reads the Python source
set is `merge<Variant>PythonSources`, and it declares the source
directories as `inputs.files(...)` -- so with `build/saathi-python`
among them Gradle 8 would have refused the build outright ("uses this
output of task ':app:syncSaathiPython' without declaring an explicit or
implicit dependency"), on the first run, before any APK. The others are
`generate<Variant>Python{Requirements,Proxies,JniLibs,<Source|
Requirements|Misc|Build>Assets}` and `extract<Variant>PythonBuildPackages`;
every one has `Python` in its name and nothing else in an AGP/Kotlin
build does, so that word alone is the match and `syncSaathiPython`
itself is the one exclusion. Lost: handing the source set a `Provider`
mapped from the Sync task so that Gradle infers the edge -- Chaquopy
collects its `SourceDirectorySet`s into `inputs.files(...)`, which
would carry the producer, but whether its own `sourceSetNames` lookup
keeps that is not something to bet the first build on when one explicit
line is certain either way.

**2026-10-08 — Review findings on the phone engine, applied: the REST
client carries the SDK client's deadline and retry.** `provider.py`
now owns one set of numbers -- `CONNECT_TIMEOUT_SECONDS = 5`,
`REQUEST_TIMEOUT_SECONDS = 20`, `MAX_RETRIES = 1`, the SDK path's since
2026-09-25 -- and builds `RestChatClient` with them, where it used to
take the client's own 30 s single attempt, so a stalled request on the
phone held a turn ten seconds longer than on the laptop. The connect
deadline is the connection class's: `http.client` has one socket
timeout for connecting and reading, so `_IPv4HTTPSConnection` puts the
connect timeout on the socket for the TCP connect and the TLS handshake
and the read timeout after. The retry is narrower than the SDK's on
purpose: one more attempt only when the request never reached the
service (`URLError`: refused, unresolvable, connect timed out), never on
a read timeout, a 5xx or a 429 -- a request that may be in flight is
not sent twice, and a retry on a timeout would double the wait the
latency budget protects. Lost: the SDK's backoff-and-retry on 429/5xx
(on the phone a 429 is a key or a tier, not a blip, and it costs a
turn). `tests/test_provider.py` asserts both clients are built with the
same numbers.

**2026-10-08 — The shared `urllib` opener refuses every redirect.**
urllib's `HTTPRedirectHandler` builds the redirected request from every
header of the original, Authorization included, so a 302 from the
service would have sent the API key (or Google's bearer token:
`google_rest.py` shares `_build_opener()`) to whatever host the
Location named -- reproduced in review with two local servers. Neither
endpoint redirects, so `_RefuseRedirects` returns None and a 3xx is
reported like any other status, key scrubbed. Lost: dropping the header
only when the host changes, as `requests` does -- more code for a case
that does not occur, and a same-host redirect of a POST is not a thing
either service does.

**2026-10-08 — An error body is read whole, then the message is cut.**
Both REST modules read `_ERROR_BODY_LIMIT` (500) bytes of an error
body and then tried to parse them as JSON, which fails for any body
longer than that (a 400 for a bad tool schema, a Google error with
`details`), leaving a cut-off JSON fragment where the service's own
message should be. Now `_ERROR_READ_LIMIT` (64 KiB) is read, the
message picked out, and only that is cut to 500. Cosmetic, since the
scrubbing still applied, but the message is what a logcat line is for.

**2026-10-08 — Transcription keyword arguments are encoded as the SDK's
multipart encoder would, or refused.** `rest_client.py` sent every
extra as `str(value)`, which turns `timestamp_granularities=["word"]`
into `"['word']"` and `stream=True` into `"True"` -- forms the service
rejects. No caller sends either; the cascade sends `model`, `file` and
`response_format` and sometimes `language`. Now a list is one `name[]`
part per item, a bool is `true`/`false`, a number is its text, and
anything else is a `TypeError` by name before any request, so a future
caller fails on the laptop and not with a 400 on the phone. Lost:
silently stringifying (the status quo) and a full port of the SDK's
encoder (nested objects, files in lists: nothing here needs them).

**2026-10-08 — `reflect.py` reads a message with no content as "".**
`json.loads(None)` is a `TypeError`, which the malformed-response guard
(`JSONDecodeError`, `AttributeError`) did not name, so a model answering
with `content: null` -- the SDK's `Optional[str]`, and the REST client's
-- crashed the background reflection instead of reflecting on nothing.
`digest.py` and `phrase.py` already did `(content or "")`; now so does
`reflect.py`, in both calls. Pre-existing on the SDK path.

**2026-10-08 — `tests/test_cli.py` fakes the local audio seams only
where they import, so the remote-mode tests run with the phone's
packages.** The shared `seams` fixture imported `saathi.audio.aec` at
the top, which imports `pywebrtc_audio` at module level, so on a
machine with only the phone's packages every test in the file errored
at setup -- the nine `test_remote_mode_*` cases included, the only
tests that document `SAATHI_AUDIO=remote`. Now `_local_audio_modules()`
returns the two modules or None, the fixture fakes them when it can,
and `remote_seams` turns them into assertions when it can (where they
do not import at all, a touch fails louder still, at the import). A
local-mode test on such a machine fails inside `build_runtime()`, as
`saathi run` would there, which is the honest result. In the minimal
venv the file now gives 8 of the 9 remote-mode tests green; the ninth
asserts "piper is in the backends on Linux with piper-tts installed",
which is a full-install contract and stays as written. The three
"Piper is always available" tests were left alone for the same reason:
they pin the Linux install, and retargeting them to "available where
piper-tts imports" is a change to what they pin, not to this step.

**2026-10-08 — `MediaController`'s `browser_available=None` stays
"assume a browser"; `cli.py`'s wiring is pinned instead.** The review
asked for "no browser" as the safer default, since a controller built
without the callback re-emits a browser play nobody can take and the
kiosk rule was reached only because `cli.py` passes
`lambda: remote_audio.attached`. Declined, for now: the default is the
constructor's documented choice, twenty-odd target-rule tests build the
controller without the callback and assert the hand-off on that
default, and rewriting their setup to make a changed default pass is
the kind of test change this job does not make on its own. What the
evidence actually names -- the kiosk rule resting on a lambda nobody
tested -- is fixed: `test_cli.py` now asserts the controller has a
browser taker exactly while a phone is attached on `/audio`. Flipping
the default remains open for a step that owns those tests.

**2026-10-08 — `gcp.json` is not removed by `stop()`; raised, not
done.** The review asked for `_tear_down()` to unlink the credential
file so the service-account key does not stay on disk after the engine
is stopped or the brain moved to another computer. `tests/test_android_entry.py`
asserts the opposite (`test_stop_closes_the_port_...`: "the credential
file is the data dir's, not the environment's: it stays for the next
start to replace or remove"), which is a stated contract of the file,
like the identity database beside it; changing that test to make the
fix pass is what CLAUDE.md forbids, so it stays as written and the
finding is recorded here for whoever owns that contract. The file is
0600 in the app's private directory: retention, not exposure.

**2026-10-08 — The `.env` parser follows dotenv's rule for quotes and
ends a JSON value where its braces balance.** `OPENAI_API_KEY="sk-one" #
openai` was stored with its quotes -- `unquote()` dropped them only when
the value's last character was the matching quote -- and every call then
failed with a 401; a one-line `..._JSON={...} # gcp` kept the comment
and was "not valid JSON" to the engine. A quoted value now ends at the
first unescaped matching quote, with a JSON object right after the
opening quote taken whole first (its own `"` are content), and whatever
follows the closing quote is dropped; a bare JSON value ends where
`jsonEnd()` balances it. Lost: keeping the last-character rule for an
apostrophe inside single quotes (`'it's'`), which dotenv does not allow
either, and escape processing (still none: the value is kept verbatim).

**2026-10-08 — The private-range rule is applied to every request a
page makes, and "Test" follows no redirect.** `shouldOverrideUrlLoading`
in both WebViewClients sees only main-frame navigations, and the
network security config permits cleartext everywhere, so an `<img>`,
`<script>` or `<iframe>` at `http://<public host>` inside either page
loaded over cleartext, and the setup dialog's probe (a default OkHttp
client) would have followed a LAN address's 302 to a public host.
`CleartextGuard.intercept()` runs from both clients'
`shouldInterceptRequest` and answers an `http://` request to a host
`EngineAddress.isPrivateLan()` refuses with an empty 403; the decision
is `EngineAddress.isCleartextOffLan`, pure and tested on the JVM. The
probe client is built with `followRedirects(false)` and
`followSslRedirects(false)`; a 3xx was already "not an answer". Lost: a
false base config with a domain list (the original reason stands), and
intercepting everything (the guard would then fetch for the WebView,
cookies and all).

**2026-10-08 — The Keys page: the clipboard is emptied after a paste,
the JSON box is masked with a verdict line under it, and the dialog's
window is `FLAG_SECURE`.** After "Paste .env from clipboard" filled the
fields, the whole file -- every key in it, read by the engine or not --
stayed on the system clipboard for the next focused app; now a fill
that touched anything calls `clearPrimaryClip()` (API 28; an empty clip
below it) and the toast says so, and the README says to delete the
message that carried the file. The service-account JSON was the one
plain field, because a masked multi-line box cannot be checked for a
missed brace; it showed the private key in clear, in every screenshot
and Recents thumbnail. Now it wears `PasswordTransformationMethod` like
the rest and the check is done for the person: `Keys.credentialShape()`
(pure, tested) says whether the box holds one JSON object whose braces
balance with nothing after it, and which `client_email` it names, and a
`TextView` under the box says that -- more than eyes on a key could
tell. The dialog window gets `FLAG_SECURE` before `show()`, for the
whole dialog rather than the Keys page alone (toggling a window flag
per tab is a second thing to get wrong, and the Brain page has nothing
a screenshot is for). Lost: a "show" toggle on the JSON box (one more
widget whose only purpose is to unmask a private key).

**2026-10-08 — `tests/test_workflows.py` reading an untracked
`android.yml`: not real.** The finding predates the commit;
`git ls-files` and `git ls-tree origin/android-app` both list
`.github/workflows/android.yml`, and the four workflow tests pass on a
clean checkout.

**2026-10-12 — android-app merged into udhibranch; one branch for laptop
and phone.** The Android app is a shell around the same engine and the
same web face (a WebView, the engine embedded via Chaquopy or reached on
the LAN), not a second product, so it lives here rather than on a
parallel "udhibranch-android" that every fix would have to be made on
twice. android-app itself is untouched. How the overlaps were settled:
YouTube is android-app's (official embed first, the real watch page only
after the embed refuses; yt-dlp and deno gone), with udhibranch's
`choose_result` (play the top result at once) on top of it. STT goes
through `_transcribe` (Groq hears, OpenAI covers; the chosen language
always sent) and uploads android-app's `_encode_for_stt` container, so
the phone's WAV fallback and the laptop's FLAC both reach Groq.
`build_client` takes retries/timeout so the fail-fast Groq client also
works over the REST client on the phone. The press handler is
android-app's `handle_input` (phone mic, a vanished socket's press
released) carrying udhibranch's ATTENTIVE/open-window/preview logic;
`begin_capture` takes the phone's mic when one is attached. The phone
gets the face's moods (`android.py` passes `emotions`). Not on the
phone: the wake word (needs a local mic and faster-whisper, which
Chaquopy has no wheel for -- remote mode has no capture source, so
`_wake_listener` returns None and the phone stays push-to-talk) and
family calls (segno/pycryptodomex aren't in the APK's pip list, and the
tunnel needs cloudflared). Two of android-app's tests were written
against Hindi/Bengali and STT without a language field; both changed on
udhibranch on 2026-10-09, and the tests were brought in line with that,
as udhibranch's own were then.

**2026-10-28 — The Android speech gate opens at RMS 100 (-50 dBFS),
not 1000 (-30 dBFS).** "Works on my phone, not my tablet": on a Xiaomi
tablet (Android 16) every hold ended "nothing said; turn ended
silently". The microphone was fine -- AudioFlinger showed the shell's
`VOICE_COMMUNICATION` capture open, unsilenced, AEC and NS attached --
but speech across a table peaked at -32 dBFS and the RMS energy gate
(the only gate on Android: no `pysilero-vad` wheel) wanted three 32 ms
chunks in a row over -30. A phone held to the mouth clears that; a
tablet never did. 1000 had come from the laptop's echo-cancelled
silence, a source this gate never sees (the laptop runs Silero). 100
passes `known_sentence.wav` at a -40 dBFS peak and stays 10 dB over the
tablet's loudest room tone (-60). Lost: an adaptive per-hold floor (an
all-speech hold has none to measure) and Android's AutomaticGainControl
(lifts the room with the voice). `audio/vad.py`'s constant comment has
the numbers; two tests pin them.

**2026-10-28 — Inside the Android app, "call my son" rings over the
device's own SIM.** For the demo on a SIM tablet the owner chose a real
phone call. Until now remote mode registered an "unavailable"
`call_contact`: Twilio needs cloudflared and the echo-cancelled pair,
the family app needs Cryptodome and a public URL, and the phone has
none of them. `call/device_phone.py`'s `DevicePhone` now stands where
Twilio's `CallController` stands (`active`, `dial`, `dial_test_number`),
so matching, relation words, choice cards, notes and the registry's
permission check are unchanged; it sends one `{"type": "phone",
"action": "dial", "number"}` frame on `/ws`, and the shell
(`PhoneCalls.kt`) waits for core.py to go free ("Calling Udhi." heard
first), then `TelecomManager.placeCall` on speaker, or the dialer with
the number filled in if `CALL_PHONE` was refused. The route is ready
only while a connected shell has said `phone_status: can_call` (SIM
ready); otherwise she hears it can't call. `save_contact` and
`answer_card` are now registered on the phone too. Lost: dialling from
Python through Chaquopy's Java bridge (the engine holding an Android
handle, and embedded-only); reporting the call's end back
(READ_PHONE_STATE; the in-call screen owns hang-up -- `active` is a 20 s
debounce instead). Three remote-mode tests that pinned "calling is off on
a phone" were rewritten to the new behaviour, not loosened: no Twilio
relay is ever started there, and a shell must say yes before anything
dials. On a Device Owner tablet the default dialer joins the lock-task
allowlist, or the in-call screen could not come up.

**2026-10-28 — The udhibranch merge broke the APK's engine at start;
fixed.** `tools/calling.py` -> `call/routing.py` imported `family.py`
and `webrtc.py` for annotations, which import `call/push.py`'s
Cryptodome, which the APK does not carry. Imports are now type-only and
`_build_family` notes an ImportError. `test_android_phone.py` now lists
Cryptodome and segno as absent, which is what caught it.

**2026-10-28 — On the Android app, calls go through Twilio from the face
page.** The owner chose Twilio over the SIM for the demo. The laptop's
Twilio path (`call/controller.py`) can't run in the APK: its audio rides a
Media Streams websocket that needs a public tunnel and the laptop's
echo-cancelled pair. Twilio's Voice JavaScript SDK (2.18.5, Apache-2.0,
vendored in `static/vendor/`) needs neither: the face page on
`http://127.0.0.1` is a secure context, so it gets the microphone, and the
call's media is WebRTC to Twilio with the browser's echo cancelling.
Twilio dials through a TwiML App whose Voice URL is a TwiML Bin
(`<Dial answerOnBridge="true"><Number>{{To}}</Number></Dial>`), so nothing
of ours is ever public. `call/web_phone.py`'s `WebPhone` stands where
`CallController`/`DevicePhone` stand for `tools/calling.py`, owns the
call's state, drives udhi's phone panel and the hold seam (End button and
2 s hold reach the same `hangup()`), and mints the access token itself
(HS256 JWT with the API key secret, an hour, outgoing only) at
`GET /phone/token`, loopback only. Same vendor, no new one; one new key,
`TWILIO_TWIML_APP_SID`. Chosen when that key and the Twilio keys are set;
else the SIM route. Frames carry `"via": "twilio"` so the shell's SIM
dialer never acts on them. Lost: a token server (one more public thing to
run); twilio-python for the token (not in the APK, twenty lines);
creating the Bin/App from the engine through the REST API (Bins aren't in
the API, and writing to her Twilio account at boot is a side effect
nobody asked for). Run once against real Twilio from the laptop's
headless Chromium (rang, answered, hung up); not yet on the tablet.

**2026-10-28 — A developer console at `/admin`, for whoever builds or sets
Saathi up.** The owner asked for pages showing how memory and personality
work, with the files editable: `screen/admin.py` plus
`static/admin/` (How it works, Memory, Personality, Contacts, Keys &
settings). Loopback only, like `/phone/token`; on the tablet it opens from
the setup dialog's "Developer console" button (`ConsoleActivity`), never
from her face. It writes only through what already exists: rules through
`IdentityStore` retire + append, with a provenance episode ("Noted by the
care team: …") so an edited rule still has a source; episodes append-only;
contacts through `call/contacts.py`'s convention (removal is a newer row
with no phone, so history stays); preferences as the existing keys. The
persona edit is `persona.txt` in the data folder, which
`compile_context` prefers over the shipped file, so it survives app
updates; Reset deletes it. After a write the engine recompiles context
between turns (`refresh_context`), never during one. Keys are shown as
set/missing, never a value: on the laptop they live in `~/.saathi/env`
(edit there); on the tablet the page saves them through a `SaathiShell`
bridge into the shell's encrypted store and the engine restarts, the
dialog's Save path. The bridge exists only in `ConsoleActivity`, which
refuses navigation off `$engine/admin`. Lost: a native Android console
(a second UI to keep in step, and the laptop wouldn't have it); editing
episodes in place (they are the record rules cite); writing keys from the
engine (it doesn't own them on the tablet, and a page that writes secrets
over HTTP is one more thing to secure).

**2026-10-28 — On the Android app, calls go through Twilio only.** The
owner: "the calls should go through Twilio only". Remote mode no longer
falls back to the device's SIM when the TwiML App is missing; calling is
off and the start-up note names the missing key. Lost: the SIM fallback
(`call/device_phone.py` and the shell's `SimDialer` stay, unused, for a
voice-capable tablet later): the demo tablet may be data-only, and a call
that quietly takes another route is harder to explain on stage than one
that says what is missing. Four remote-mode tests in `test_cli.py` and one
in `test_android_entry.py` pinned the fallback and were rewritten for this.

**2026-10-29 — The engine's pages go out `no-store`; the shell clears its
WebView cache at start.** On the tablet, after updating the APK, the face
ran the pre-merge `roboeyes.js` (every state change threw
`setBlush is not a function`) and a `main.js` from before Twilio, so calls
never started. Cause: inside the APK every file's mtime is 1980, so the
WebView cached them heuristically for years. `screen/server.py` now sends
`Cache-Control: no-store` for `/`, `/static/` and `/admin`, and
`MainActivity` clears the cache once at start for what older engines
left behind. Lost: `no-cache` with revalidation (the ETag from a frozen
mtime misses a same-size edit) and versioned file names (every import in
the face would change).

**2026-10-29 — The udhibranch YouTube changes are reverted; the rest of
the merge stays.** The owner: YouTube on the tablet "is not functioning
properly", go back to the previous commit for that part only.
`tools/media.py` and `static/js/media-policy.js` (and their tests) are
d9a00e1's again: a search offers up to three results to choose from
instead of `choose_result` playing the top one, and the duck is back to a
fifth in listening/thinking/speaking, not a tenth from `attentive`.
`test_media_choosing.py` went with `choose_result`. The transcriber's
media-command prompt while music plays (`MEDIA_STT_PROMPT`) is gone too:
Whisper answers unclear audio with a prompt's own words, a suspect for the
"gibberish" replies. Kept: the router's extra media phrases and mid-song
"Kaki, stop" (tangled with the Kaki rename; additive), the server's
media-aware follow-up window (inactive in remote mode). Also: every turn
now logs what was heard with the clip's length and loudness, so a reply
that makes no sense can be traced to a mishearing or to the model.

**2026-10-29 — A settings gear on the tablet's screen, and an "Under the
hood" tab in the console.** The owner asked for a visible settings button;
the five-second hold was too hidden to find. A dim gear (35% opacity, no
text, never changing with her state) sits top-right: tap opens the
developer console, long press the setup dialog. This reverses the setup
dialog's "no control on her screen that is not for her" for one small
icon; lost: the hold alone (still there). The new tab explains, from the
code's own constants and the store's own schema, what makes up her
personality each turn (persona, memory instruction, rules, episodes,
length cap, conversation, language, mood, her words), how episodes are
scored, where everything is stored, and each table: purpose, who writes
it, what reaches the model. It says plainly that automatic learning
(`identity/reflect.py`) is not run by the app yet: rules come from the
console and her corrections.

**2026-10-29 — The shell asks for MODIFY_AUDIO_SETTINGS: Twilio calls from
the tablet never reached Twilio without it.** Every "call my son" ended in
under a second and Twilio had no record of it. In the WebView, getUserMedia
failed ("NotReadableError: Could not start audio source"); Chromium's own
log said why: "Requires MODIFY_AUDIO_SETTINGS and RECORD_AUDIO". The
manifest had only RECORD_AUDIO. It is an install-time permission, so no
prompt. With it, a test call from the tablet's page opened the mic, reached
Twilio and rang. The SDK reports "disconnect" before the "error" that
caused it, and `WebPhone` dropped that error as stale, which is why the
log said only "the call ended": a late failure is now logged with its
reason.

**2026-10-29 — A developer switch keeps the last ten turns' audio; turning
the volume up before Whisper was measured and lost.** On the tablet, "I'm
feeling fucked up today" was transcribed "I think that's a great deal." and
the model answered that sentence ("I'm glad you feel that way"): the reply
was right for what it was given; the hearing was wrong. Every tablet turn
logged that day arrived quiet (rms 115-205 of 32767), so normalising the
level before upload was the obvious fix. It was measured first: the repo's
two test sentences, turned down to rms 80-150 with background noise, gave
Whisper the same transcript raw and boosted 20-40x, and the errors followed
the noise, not the level. So the cause is in the audio itself (distance,
room noise, or the platform's noise suppression on top of
VOICE_COMMUNICATION's), and that needs the real clip. With
`<data dir>/debug/keep-turn-audio` present, each turn past the speech gate
is written to `<data dir>/debug/turn-<time>.wav`, the newest ten kept, off
the hot path. Off by default: her voice is not kept on disk unless a
developer asks.

**2026-10-29 — Twilio calls from the tablet are raised in the page: the
caller x3, her mic x2, each through a limiter.** The owner found calls very
quiet and asked for more mic pickup. The tablet's voice-call volume was
already at its maximum (11/11, speaker), so there was no setting left to
turn up; the Twilio SDK's AudioProcessor hooks (local and remote) carry a
Web Audio gain and a -6 dB limiter instead. Any failure (a context that
won't run, an SDK without the hooks) leaves the call its own stream.
Measured in the tablet's WebView on a quiet tone, the limiter's make-up
gain adds ~x1.5: the caller comes out ~x4.4 (+13 dB), her mic ~x3
(+9.5 dB); loud speech is held near -3 dBFS. Lost:
playing calls on the media stream, which leaves the communication mode the
platform's echo cancelling runs in. Saathi's own listening is untouched: a
kept tablet turn was clean (background gated to zero, transcribed right),
and boosting quiet audio was measured not to change Whisper's transcript.

**2026-10-29 — Captions switched in the console reach the open face at once.**
The owner asked for a subtitles option on the tablet. Captions existed
(captions.js, the `captions` preference) but only the Ctrl+L panel
broadcast a change, and the tablet has no keyboard; the console's Captions
setting was written but the face learned it only on its next reconnect.
The console now takes a `settings_changed` callback, and the server passes
one that resends the settings message to every open face.

**2026-10-29 — Every exchange, her clip and the memory file are copied to
AWS S3 (owner's call); turbo Whisper stays.** The owner asked for all
conversations and data to be kept in the cloud, readable later by a
caregiver/family portal, and chose AWS, uploads after every turn, and
transcripts + memory + voice clips. `cloud/sync.py` queues each exchange
(never on the turn's path), writes it to `<data dir>/cloud/outbox`, and a
worker thread PUTs it to `devices/<device-id>/{transcripts,audio,memory}/...`
(layout in that module and docs/CLOUD.md); offline, the outbox waits and
goes up in order, across restarts. The memory file goes up as a
consistent sqlite backup, 20 s after the turn (so its episode is in) and
at most every 5 minutes, as `identity-latest` plus one per day. Each
exchange is also appended to `<data dir>/transcripts/<day>.jsonl` on the
device. Only S3, and only PUT: no boto3 (tens of MB on a 62 MB APK for one
request; `cloud/s3.py` signs SigV4 itself, matched against botocore on 200
random requests and pinned by AWS's published example). Off unless all
four `AWS_*` keys are set. The device stays the source of truth; SPEC.md's
"lives where the device is" needs a line for the copy -- proposed to the
owner, not applied. The mishearings that prompted the look were Punjabi
song names forced through English-only turbo Whisper ("Ve Kangna tera ni"
-> "I don't care about your money."); full whisper-large-v3 spelled them
out, but at ~+250 ms a turn and ~2.8x the price, and the owner kept turbo.
Also fixed: `screen/server.py`'s `run()` took no `admin_info`, which
`cli._run` passes since the console landed, so `saathi run` stopped with
a TypeError before the face came up. KeysTest's pinned list of the names
the tablet stores was extended by the four AWS names: the owner changed
what the device holds, so the test changed with it.

**2026-10-29 — "Melody" wakes the tablet, spotted on the device with
sherpa-onnx.** The owner asked for "Melody" as the tablet's wake word, the
best way. The laptop's listener (Silero VAD + faster-whisper tiny.en)
cannot run in the app: the embedded Python has no onnxruntime or
ctranslate2 wheels. So the shell spots the word itself: sherpa-onnx's
keyword spotter (v1.13.8, Apache-2.0; Kotlin API vendored unmodified under
`com/k2fsa/sherpa/onnx`, arm64 JNI libraries in `jniLibs`) with the
3.3M-parameter gigaspeech zipformer (Apache-2.0, ~5 MB int8 in
`assets/kws`), told the word as tokens (`▁ME LO D Y`). Measured on the
laptop with the same model and settings: 36/36 synthetic "Melody, ..."
clips across six voices (35/36 at the tablet's quiet level); no wake in
204 s of ordinary talk or in nine of her real turns; the one near-miss is
"malady" (6 of 60 negatives), which the engine's second check
(`mentions_name`, similarity 0.67 < 0.88) drops silently. On the tablet,
2 of 3 clips played from the laptop's speaker across the desk woke it and
were answered. The engine decides when the tablet may listen
(`{"type":"wake","listen":...}` to sockets that `subscribe`): the
`wake_word` setting on (console, default on), idle or asleep, no call on
screen. A wake presses with `"by":"name"`, so the turn gets the same
second check as the laptop's. The turn's audio is the 2.5 s before the
spot (the name, which that check needs) plus what follows, until an
energy endpointer hears 0.9 s of quiet. The x86_64 emulator build has no
wake word (no native library there); the button works everywhere. Cost:
the spotter on every frame was ~31% of one tablet core, so frames the
noise suppressor has gated to silence (rms <= 4) are skipped past a 1 s
tail. The idle mic thread is now 0.8% of a core. The APK grew from ~62 to
~97 MB, mostly onnxruntime (22 MB). Lost: Picovoice Porcupine (a new
vendor with an access key: asked-first territory), Android's
SpeechRecognizer in a loop (not made for always-on; no reliable
on-device recogniser here), and sending audio to the cloud to find the
name (nothing leaves the device before she calls). With the language
pinned to Hindi, Whisper writes the name in Devanagari (22 of 36 clips:
मेलदी, मेलोड़ी, ...), which the second check would have thrown away;
those spellings now count and are cut like the Latin name (31 of 36 now
pass, from 9). The persona and captions still call her "Kaki": renaming
her changes what she hears, so it is the owner's call.
