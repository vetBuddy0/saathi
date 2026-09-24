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
