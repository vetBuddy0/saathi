# Screen — accessibility cards + YouTube panel beside the face

Stream A (2026-09-25). Two things now sit beside the face and are
relayed by `screen/server.py` the same way state is: the four
**cards** (`screen/cards.py` + `static/js/cards.js`, the module every
other stream imports to ask her something) and the **YouTube panel**
(`tools/media.py` + `static/js/media-panel.js`). Neither ever replaces
the face.

This is the first instance of the ambient content layer TODO.md says
was never built: on-screen text for *content* — a question with her
options, a title, a number — never status.

## What was built

### Cards (`screen/cards.py`, `static/js/cards.js`)

Four primitives, nothing more: **Choice** (two or three numbered,
tappable options), **Confirm** (one statement, yes or no), **Read-back**
(one value very large, digits grouped), **Holding** (progress while the
spacebar is held). Plain HTML and CSS matching the face; no component
library — none is built for a 75-year-old across a room.

Rules, enforced in code and pinned by tests, not just documented:

| Rule | Where it's enforced |
|---|---|
| Three options maximum, ever | `choice()` raises `TooManyOptions` on a fourth; the caller picks the best three and says so |
| Numbered so "the second one" works by voice | `Option.n`, drawn as `1/2/3`, spoken as "One: …" |
| Voice and touch always both work | one entry point, `CardController.answer(id, answer, source=)`; tap (`card_answer` over the socket) and voice (a tool) both call it |
| Everything spoken as well as shown | every `Card` has `spoken`; `show()` refuses an empty one |
| Targets ≥ 100 px tall | 112 px, asserted from `getBoundingClientRect` in a real 1080p headless Chromium |
| Text ≥ 32 px | 40/36/32 px floors, asserted from `getComputedStyle` |
| Contrast AAA (7:1) | colour tokens in `:root`; `tests/test_cards_style.py` computes every pair |
| No hover states | no `:hover` on any card selector (tested) |
| Nothing times out or vanishes | no timers in `cards.py` or `cards.js` (tested by grep); `ask()` has no timeout unless given one |
| One card at a time | `show()` replaces; a replaced card is released as a dismiss |
| Always a way out | a full-width "Never mind"/"Okay" on every answerable card; Holding's way out is letting go |
| Tap only | `click` listeners only; no drag/move/long-press (tested) |
| Beside the face, never instead | `body.card--open` gives the face the left half; the face is in every layout |

**Cards are content and interaction, not the status text SPEC.md
forbids.** "Listening…" describes the device; "Which Priya?" with
three numbered names addresses her and waits for her answer. Someone
will read "no text on screen" and want to strip this; the module
docstring says why not.

### The cards interface (what Calling imports)

```python
from saathi.screen.cards import (
    CardController, HoldController, Answer, TooManyOptions,
    choice, confirm, readback, holding,
)
```

Builders — each returns a frozen `Card` with `.id`, `.kind`, `.title`,
`.spoken`:

- `choice(title, [label, label, (label)], spoken=None)` — 2–3 options,
  numbered 1..3; **4+ raises `TooManyOptions`**. Default `spoken` is
  the title followed by "One: …. Two: …."
- `confirm(statement, spoken=None)` — yes/no.
- `readback(title, value, spoken=None)` — digits grouped
  (`"0412345678"` → `"041 234 5678"`); default `spoken` reads the
  digits one at a time with a pause per group.
- `holding(label, progress, card_id=None)` — used by `HoldController`;
  not built by hand.

`CardController` — one instance, created in `cli.py`, passed as
`build_app(..., cards=cards)` / `run(..., cards=cards)`:

- `show(card) -> id` — replaces whatever is up.
- `clear()`.
- `answer(id, answer, *, source="tap"|"voice"|...) -> bool` — the one
  door for tap and voice. `False` for a stale id or a malformed shape
  (dropped; nothing happens).
- `on_answer(callback) -> unsubscribe` — `callback(Answer)`; `Answer`
  has `.card_id`, `.kind`, `.answer`, `.source`, `.card`, and
  `.choice` / `.yes` / `.dismissed`.
- `ask(card, timeout=None) -> Answer | None` (blocking, thread-safe,
  no timeout by default) and `async ask_async(card)`. **Never inside
  `end_turn()`**: that holds the turn in THINKING with the mic closed,
  so she could only answer by tap. A tool does `cards.show(card)` and
  returns `card.spoken` in its note; her spoken answer arrives on the
  next turn and the tool calls `cards.answer(id, {...},
  source="voice")`. `ask()` is for code between turns (initiative, a
  call's own state machine).
- `current`; `set_broadcast(fn)` (the server installs it).
- Accepted answer shapes: choice → `{"choice": 1..len(options)}`;
  confirm → `{"yes": bool}`; any kind → `{"dismiss": true}`; readback
  and holding take dismiss only.
- Every method is safe from the executor thread and from the loop.

`HoldController(cards)` — create alongside; pass as `build_app(...,
hold=hold)`:

- `set_handler(on_complete, *, seconds=2.0, label="Keep holding")` —
  `on_complete()` takes no arguments and fires exactly once per
  completed hold.
- `clear()` — the spacebar is a spacebar again.
- `active` (a handler is set), `holding` (a press is in progress).
- While active: a short press/release does **nothing** (`core.py`
  never hears it — no turn, no capture); a press held ≥ `seconds`
  fires once; a Holding card with `label` shows progress 0→1 from a
  server-side 100 ms timer measured from the press timestamp (the
  browser sends exactly one press and one release per hold); releasing
  before the threshold clears the card and fires nothing.
  `begin()/tick()/abandon()` are the server's to call.

Message shapes (`screen/server.py`):

- server → browser: `{"type":"card","card":{"kind":"choice|confirm|readback|holding","id":"…","title":"…","spoken":"…","options":[{"n":1,"label":"…"}],"value":"041 234 5678","progress":0.6}}`
  (`options` only on choice, `value` only on readback, `progress` only
  on holding); `{"type":"card","card":null}` clears. **Never sent on
  connect** — connect is still exactly `state` then `settings`.
- browser → server: `{"type":"card_answer","id":"…","answer":{"choice":n}|{"yes":bool}|{"dismiss":true}}`.
- A voice answer never crosses the socket.

What Calling must do (so it doesn't invent its own):

1. `cli.py`: `cards = CardController(); hold = HoldController(cards)`;
   pass both to `run(...)` (the diff below does this).
2. "Which Priya?" → `choice("Which Priya?", [three labels])` →
   `cards.show(card)` → return `card.spoken` in the tool's note. On "the
   second one" next turn → `cards.answer(id, {"choice": 2},
   source="voice")`. Register `on_answer` to act on taps.
3. "Call Priya?" → `confirm(...)`, same flow; `{"yes": True/False}`.
4. Read a number back → `readback("Priya's number", "0412345678")`.
5. Hang-up: `hold.set_handler(hang_up, seconds=2.0, label="Keep holding
   to hang up")` when the call connects; `hold.clear()` when it ends.
6. The engine says `card.spoken` (as the tool's note, or
   `session.say(card.spoken)` between turns). The controller never
   speaks.

### YouTube (`tools/media.py`, `static/js/media-panel.js`, `media-policy.js`)

`play_music` made real — same name, same `"music"` permission as the
stub, so `Registry.call` gates it as before. One tool with an `action`
vocabulary that matches how she talks: `search`, `play` (with `choice`
1–3, or none for "that one"), `pause`, `resume`, `stop`, `louder`,
`quieter`, `bigger`, `smaller`, `again`, `next`, `never_mind`.

- **Search**: YouTube Data API v3 `search.list` over `urllib`
  (`videoEmbeddable=true`, `maxResults=3`, 100 quota units a call; the
  tool never loops). Key from `YOUTUBE_API_KEY`, never logged, the URL
  never logged. One real response is the fixture
  (`tests/fixtures/youtube_search_old_chinese_songs.json`, key-free).
- **Results stay referenceable** for the rest of the conversation —
  `MediaController.last_results` is what "the second one", "that one",
  "play that again" and "next" resolve against, however many turns
  later. This was the brief's protect-this item.
- **The offer is a Choice card** when a `CardController` is wired (Part
  B): the three titles, numbered, tappable, dismissable; the card's
  `spoken` is returned as the tool's note. A tap plays that result
  (the tap is the browser's user gesture); "the second one" by voice
  answers the same card through the tool; "never mind" (spoken →
  `never_mind`; tapped → dismiss) clears the card and leaves the
  results referenceable. Without cards the panel draws its own results
  view — the pre-Part-B behaviour, kept for a screen without cards.
- **Playback**: IFrame Player API in a panel beside the face (right
  half); `bigger` fills the screen with the face at 22vw×22vh in a
  corner; `smaller` brings it back. The iframe is created once and only
  hidden thereafter. Playback always starts from the message handler
  that follows a press or a tap — never from a timer; the first video
  does not start until its volume is set. The API script loads lazily
  on the first play so the face needs no network.
- **Ducking, not pausing**: on `state: listening` the browser drops the
  player to 20 %, holds it through thinking/speaking, restores at idle
  (`media-policy.js`). No PLAYING state was added to `core.py`.
- Volume tracked in the tool (70, ±15, floor 10 — "quieter" never
  reaches silence). Titles trimmed to 60 chars. An unplayable video
  (player error) is never offered again. A reloaded page sends `reset`
  so "carry on" starts the video again instead of insisting it's on.
- Model probe (`qwen/qwen3.8-27b`, text only): correct action on all
  six turns (search → play 2 → quieter → bigger → again → stop). Its
  first offer read one title of three; the note now asks for all three
  explicitly. Groq's output-tokens-per-minute limit on this account
  429'd one two-completion tool turn — worth watching in real turns.

## Decisions

All in `DECISIONS.md` under 2026-09-25: the `"music"` grant reversal;
one tool with an action enum; results in the controller; duck 20 %,
never pause; a new search stops playback; the server owns the seam;
volume in the tool; JS tested in Chromium not node; fullscreen face
corner; cards as a `screen/` module; no truncation on a fourth option;
`ask()` not inside `end_turn()`; replaced cards released as a dismiss;
hold takes the press away from core; card sizes and tokens; the offer
as a Choice card; tap vs voice through the same door; `tools/media.py`
importing `screen/cards.py`.

## Code review (whole branch, `main...HEAD`)

Ten findings; seven fixed with tests, three answered in DECISIONS.
Fixed: a single search result raised inside `choice()` (now a Confirm
card); `speak_value` spelled "plus" letter by letter; `group_digits`
regrouped decimals and times; a hold handler set or cleared while the
key was down stranded core.py or orphaned the hold timer (releases now
follow their press; the timer exits on abandon); the IFrame API script
failing to load queued every later play forever (now reports an error
and frees the panel); `CardController` broadcast outside its lock (now
inside); a tap on an already-unplayable result said nothing (such
results are no longer offered). Answered: the tap path's permission
check (DECISIONS: the tap answers a gated call's question); the
`ORDINALS`/spoken-form duplication between `media.py` and `cards.py`
(left; noted in debt); the docstring claiming `cli.py` registers the
tool (reworded to "is to").

## Two test-assertion corrections (step 0)

Both were wrong assertions, not wrong code; neither test was changed
to hide a failure:

1. `test_the_first_video_does_not_start_before_its_volume_is_set`
   asserted `["new"]` in the pre-ready snapshot. `new YT.Player` runs a
   microtask after `await loadIframeApi()`, so the snapshot is
   legitimately empty. The assertion now says what the test's name
   says: no volume/play call before ready.
2. `test_tool_survives_the_models_stray_or_mistyped_arguments` expected
   `"none"` for `query=5`. The fake search ignores the query, so `"ok"`
   is right; the assertion now checks the coercion (`last_query ==
   "5"`), which is what was under test.

## AEC — measured; does not cover browser audio

Chrome's stream lands on the **raw** sink (Sink 1,
`alsa_output…analog-stereo`, the PulseAudio default), confirmed three
times with `pactl list sink-inputs` while a page played; `paplay
--device=<echo-cancel sink>` (what `playback.py` does) lands on Sink 2.
`module-echo-cancel` only references audio that passes through its own
sink, so by construction YouTube audio is not in the AEC reference and
feeds straight back into the mic.

ERLE could **not** be measured acoustically on this box: headphones are
plugged in (`Active Port: analog-output-headphones`, speaker port "not
available"; mic on the headset port at 27 %), so there is no
speaker→mic path — raw-mic RMS sat at the 0.0003 noise floor in every
run, including a silence control. The routing finding stands on its
own. Mitigation shipped on the screen side: duck to 20 % while
listening. The fix is in `audio/` (below).

## Screenshots

`/tmp/saathi-screen-scratch/shots/cards-{choice,confirm,readback,holding}.png`
and, from the YouTube pass,
`.../scratchpad/youtube/media-{panel,playing,fullscreen}.png` (face
visible in every one; large type; no status labels). Reproduce with
`?demo=cards-choice|cards-confirm|cards-readback|cards-holding|media|media-playing|media-fullscreen`
on a running server (dev only; the kiosk never passes it).

## Could not verify

- The live voice path end to end: `cli.py` is not wired (diff below).
- The three-title offer under the real `compile.py` prompt; the probe
  used a stand-in system prompt.
- Sticky user activation in a plain (non-kiosk) browser; the kiosk
  passes `--autoplay-policy=no-user-gesture-required`.
- The IFrame API against real YouTube (region blocks, error 150/101).
- Anything on arm64 — no Pi here; nothing new is platform-specific.

## Debt

- The tests re-implement `cli.py`'s `handle_intent`; a
  `make_intent_handler(registry, granted)` in `tools/registry.py`
  would remove the duplicate (registry is shared territory).
- `media-panel.js` caps at three with `.slice(0, 3)`, duplicating
  `MAX_RESULTS`; `ORDINALS` and the "One: title" spoken form exist in
  both `tools/media.py` and `screen/cards.py`.
- A tap on a result that then errors in the player (first failure)
  gives her no spoken word; the next voice turn is told, and the
  result is never offered again.
- `cards.js` re-derives nothing, but `readback` grouping is Python-side
  only; a Calling screen that needs live grouping would call it.

## Cross-territory edits needed

### `saathi/cli.py` — wire media, cards and hold

```diff
@@ def _run() -> int:
     from saathi.config import Config
     from saathi.core import Core
     from saathi.identity.store import IdentityStore
+    from saathi.screen.cards import CardController, HoldController
     from saathi.screen.server import run
+    from saathi.tools.media import MediaController
 
     config = Config.load()
     core = Core()
 
     store = IdentityStore(config.identity_db_path)
     store.create()
 
+    # Beside the face: the cards every stream asks through, the
+    # hold-to-confirm seam Calling uses for hang-up, and the media
+    # controller. Built before the session so the tools can close over
+    # them; the screen server installs their broadcast at startup.
+    cards = CardController()
+    hold = HoldController(cards)
+    media = MediaController(cards=cards)
+
     session = None
     capture_source_id = None
     if os.environ.get("GROQ_API_KEY"):
@@
         from saathi.tools.language import SET_LANGUAGE_DESCRIPTION, make_set_language_tool
         from saathi.tools.llm_schema import tool_to_openai_schema
+        from saathi.tools.media import MEDIA_DESCRIPTION, make_media_tool
         from saathi.tools.registry import PermissionDenied, Registry, UnknownTool
@@
         registry = Registry()
         set_language_tool = make_set_language_tool(store)
         registry.register(set_language_tool)
-        granted_permissions = frozenset({"preferences"})
+        media_tool = make_media_tool(media)
+        registry.register(media_tool)
+        # "music" granted (DECISIONS 2026-09-25): playing a song on her
+        # own screen has no consequence outside the room; "calls" stays
+        # ungranted until Calling lands its own gate.
+        granted_permissions = frozenset({"preferences", "music"})
@@
-        tool_schemas = [tool_to_openai_schema(set_language_tool, SET_LANGUAGE_DESCRIPTION)]
+        tool_schemas = [
+            tool_to_openai_schema(set_language_tool, SET_LANGUAGE_DESCRIPTION),
+            tool_to_openai_schema(media_tool, MEDIA_DESCRIPTION),
+        ]
@@
     run(
         core,
         config.screen_host,
         config.screen_port,
         session=session,
         capture_source_id=capture_source_id,
         store=store,
+        media=media,
+        cards=cards,
+        hold=hold,
     )
```

### `saathi/audio/aec.py` — route default clients through the echo-cancel sink

Chromium chooses no sink; it plays through PulseAudio's default. Make
the discovered echo-cancel sink the default once the pair exists (no
device name is written anywhere — `handles.sink_id` is whatever
PulseAudio reported):

```diff
@@ class SystemEchoCancel:
     def ensure(self, mic_id: str, speaker_id: str) -> EchoCancelHandles:
-        return self.find(mic_id, speaker_id) or self.load(mic_id, speaker_id)
+        handles = self.find(mic_id, speaker_id) or self.load(mic_id, speaker_id)
+        # Clients that pick no device (Chromium, for YouTube audio)
+        # play through the default sink. If that is the raw speaker
+        # the AEC never sees their audio as a reference and the mic
+        # hears it back (measured 2026-09-25: Chrome landed on the raw
+        # sink; see docs/completed/screen.md). Pointing the default at
+        # the echo-cancel pair routes them under the AEC. Idempotent.
+        self._run(["set-default-sink", handles.sink_id])
+        self._run(["set-default-source", handles.source_id])
+        return handles
```

`tests/test_aec.py` will need the fake `run` to accept the two new
commands (they return nothing). Verify on the box with `pactl info |
grep Default` after `saathi run`, then `pactl list sink-inputs` while
a video plays: Chrome's stream should show `Sink: <echo-cancel sink
index>`.

### Proposed SPEC.md addition (not applied)

Under "The face", after "No status text under the face":

> **Cards.** Four on-screen primitives — Choice (two or three numbered
> options), Confirm (yes or no), Read-back (one value, very large),
> Holding (progress while the button is held) — for resolving
> ambiguity by tap or voice. Three options maximum; numbered so "the
> second one" works by voice; spoken as well as shown; tap targets at
> least 100 px, text at least 32 px, contrast AAA; no hover, no
> timeouts, one card at a time, always a way out, tap only. They sit
> beside the face and never replace it. **Cards are content and
> interaction, not the status text this section forbids**: a label
> describes the device; a card asks her something and waits for her
> answer. Every stream that asks goes through `screen/cards.py`.

Under "Scope": music moves from *Out* to *In* ("YouTube, official APIs
only, in a panel beside the face"); calls stay out. In the interfaces
table: `Tool | builtin, stubs, media`. Under "Audio": "AEC covers a
browser's audio only if its stream is routed through the echo-cancel
sink; the default sink is set to it for that reason."


## Fixed later (2026-09-26)

Reported after the live run: cards vanished, taps didn't register, she
kept talking after a pick, the player never appeared, titles were
unreadable. Reproduced in a real headless Chromium against the real
page and server (node Playwright driving the pre-installed Chromium;
YouTube itself is unreachable from the build container, so the IFrame
API and the embed page were stand-ins speaking the same postMessage
protocol). What was found and changed:

- A tap on the offer card while the three titles were still being read
  cleared the card and started the video, and the reading carried on.
  An accepted tap on a card shown by the live turn now ends that turn
  (`server.py`, `end_turn_answered_on_screen`).
- A re-search replaces the card with a new id; a tap on the old one is
  stale and was dropped silently. It is now logged, and the media card's
  id is the tool's before `show()` (TODO M9).
- The card was not re-sent on connect, so a reload lost the question.
- `play` by voice came back through the model with "say one short
  thing"; it now returns `say: ""` and the turn ends with nothing said.
- `[hidden]` lost to `.media-results { display: flex }` in the real
  stylesheet.
- The panel could wait forever for an API script that loaded but never
  announced itself, or a wrapper that never called `onReady`; both now
  fail by deadline with a code, and the next play starts fresh.
- Search candidates are checked with `videos.list` for
  `status.embeddable`; a video the player still can't play is
  re-offered without it, on the card.
- Titles are cleaned (`clean_title`) and each sentence is read by the
  voice of its own script.
