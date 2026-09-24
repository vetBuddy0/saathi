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
