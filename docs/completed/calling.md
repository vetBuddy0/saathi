# Calling — Twilio Media Streams, contacts as entities, name matching

Stream B. Branch `batch/calling`. Owns `saathi/call/` and
`saathi/tools/calling.py`. Relay port 8768.

She can say "call my daughter" or "call Vasudev" and the right phone
rings. The audio runs through the same echo-cancelled mic and speaker
as the cascade. She hangs up by holding the spacebar for two seconds.
A number is saved by voice and read back digit by digit on a card
before anything is written. Nothing here is wired into `saathi run`
yet: that needs the `cli.py` diff below, which is another stream's
file.

## What was built

| Module | Why it exists |
|---|---|
| `call/codec.py` | G.711 μ-law and 16k↔8k resampling in numpy. Bit-exact with the reference (`audioop` used as the test oracle over all 65536 inputs). Not `audioop` itself: it is removed in 3.13. |
| `call/audio.py` | `CallAudioBridge`: `Capture` on the echo-cancelled source → 20 ms μ-law frames out; inbound μ-law → `PcmSinkWriter` (`pacat --playback`, stdin-fed) on the echo-cancelled sink. There was no streaming playback anywhere before this. |
| `call/relay.py` | `Relay` protocol (`public_url`, `start`, `stop`); `CloudflaredQuickTunnel`; `FakeRelay`. |
| `call/twilio.py` | Twilio REST without the SDK: create, complete and fetch a call. Every error is re-raised `from None` as a `TwilioError` carrying only the operation and HTTP status. Credentials have a redacted repr. |
| `call/media.py` | aiohttp `/twiml` (`<Connect><Stream>`, bidirectional), `/media` WebSocket, `/healthz`; `MediaServer` on its own thread and event loop. |
| `call/hangup.py` | `HoldSeam` protocol (matches PR #3's `HoldController`) + `FakeHoldSeam`. A 2 s hold hangs up; a tap does nothing. |
| `call/controller.py` | One call at a time: IDLE → DIALLING → IN_CALL. `active` is the "a call is happening" signal. |
| `call/numbers.py` | Spoken number → digits. |
| `call/phone.py` | Country inference and completeness; read-back grouping and spelling. |
| `call/contacts.py` | Contacts as `entities` + `edges`; latest-wins reads. |
| `call/saving.py` | `SaveFlow`: a draft across turns, a Read-back card, and a write only on yes. |
| `call/match.py` | Sound folding + Jaro-Winkler, three bands. |
| `call/choosing.py` | `ChoiceFlow`: unsure matches go on a Choice or Confirm card; dials only after her answer. |
| `call/cards.py` | Stub of PR #3's `saathi/screen/cards.py`, same shape. To be deleted at merge. |
| `tools/calling.py` | `call_contact` (replaces the stub's handler; `"calls"`), `save_contact` (`"contacts"`), `answer_card` (`"calls"`). |

Tests: `tests/test_call_{codec,audio,relay,twilio,media,controller,numbers,phone,contacts,saving,match}.py`
and `tests/test_tools_calling.py`. Full suite: 460 passed, 2 skipped;
`ruff` and F821 clean. No test mocks `subprocess`. The sink writer runs
a real `cat` child, and the tunnel runs a real stand-in script. The
media socket is driven by a fake Twilio peer over aiohttp's
`TestClient`, and handlers and card answers are exercised from worker
threads.

## Stage by stage

**Stage 1: audio to one test number.** Verified live, twice, on
2026-09-25. "Call the test number" rings `TWILIO_TEST_NUMBER`. On both
calls the person at the phone heard the Piper-synthesised sentence
injected into the call. The second call was hung up from our side:
`simulate_hold(2.0)` → REST `complete_call` → `completed`, 31 s, and
the far end had not hung up first. Inbound speech from the far end was
received and played into the echo-cancel sink.

**Stage 2: contacts in `IdentityStore`.** No new table:

- She is one `entities` row, `kind="self"`, the `src` of every
  relationship.
- A person is `kind="person"`, with the phone as JSON in `notes`
  (`{"phone","country"}`, the only free field).
- A relationship is `edges(self, person, "daughter", since, until=None)`.

"Call my daughter" follows the edge. Nothing that builds the model's
context reads `entities`, so a stored number never reaches the model.

**The store can express contacts, but only one way.** It is
append-only on `main` and `edges` has no id. A corrected number is
therefore a new row with the same name, and every read is latest-wins:
the highest id per normalised name, the newest open edge per relation.
A relation resolves to a person through the name, so an old edge still
reaches the new number. The history rows stay. When `retire()` lands,
superseded edges can get `until`.

Saving by voice:

- **Parsing.** Handles "double seven", "triple oh", "oh"/"nought" for
  zero, "nine one" = "ninety-one" = 91 ("ninety" alone = 90), teens,
  "hundred", pauses (commas, ellipses, "then", "um"), numerals and
  "plus"/"zero zero". Homophones ("for", "to") are reported as unknown
  words, never turned into digits.
- **Country.** A number without "plus" gets its country from:
  1. a stored `country` preference, learned on a confirmed save;
  2. then the device timezone;
  3. then her language, only where it names one country.

  If none gives an answer, she is asked. An inferred code is always
  said: "That's a Singapore number, plus six five."
- **Asking only for what's missing.** A draft persists across turns
  (10 min). A fragment after a pause is appended only while the number
  is still too short. A name *or* a relation is enough.
- **Read-back before every save.** Nothing is written until she says
  yes. A no keeps who it was and asks for the number again.

**Stage 3: finding the right person.**

- **Scoring.** Normalise (NFKD, accents, pinyin tones), then fold
  sounds (ph/dh/th/bh/sh/zh/ch, q, x, k, ee, oo, v/w→b, y→i, final ng,
  doubled letters), then Jaro-Winkler. Both orders of a two-word name
  are scored, plus each word of a saved name for a one-word request.
  The score is the maximum.
- **Thresholds, from a 41-pair corpus.**
  - Same person scores ≥ 0.956.
  - Ask-her pairs score 0.800–0.925.
  - Different people score ≤ 0.783.
  - **Confident** is ≥ 0.93 *and* 0.05 clear of the runner-up.
    **Unsure** is ≥ 0.79.
- **Confident:** "Calling Basudeb.", then the dial.
- **Unsure:** the closest two or three on a Choice card, spoken as
  "the first, …, the second, …", answerable by "the first one"; a
  single sub-confident name gets a Confirm card instead. Nothing is
  dialled until she answers.
- **None:** said plainly, with an offer to save a number. Never a
  guessed dial.

## Decisions, and the options that lost

All are recorded, dated, in `DECISIONS.md`. In short:

- **Media Streams** over SIP (registrar, NAT, media stack) and LiveKit
  (a second vendor and service).
- **numpy codec** over `audioop` (gone in 3.13) and a resampling
  dependency.
- **Quick tunnel behind a `Relay` protocol** over ngrok (new vendor,
  interstitial page), an SSH reverse tunnel (a VPS and a key on the
  device) and a named tunnel (needs a Cloudflare login; the production
  shape).
- **Media server on its own loop.** It must not share the face's 100 ms
  loop.
- **`pacat` writer in `call/audio.py`** until `play_stream()` exists in
  `audio/playback.py` (diff below).
- **No new core state, and `HANDOFF` not repurposed** (proposal below).
- **Two-second hold, not a double-tap.**
- **API key + secret, not the auth token.** Signature validation is
  therefore the production relay's job.
- **The model passes the number verbatim.** It must never "correct" a
  digit.
- **Country by timezone before language.** A Mandarin speaker in
  Singapore is the core case.
- **Exact names go through the matcher**, so a saved sound-alike still
  makes her choose.
- **A tap-chosen call rings without "Calling X." being said.** Nothing
  may speak outside a turn; the tapped card shows the name.

**Correction on record.** The cards stub first assumed Choice answers
counted from 0, and `answer_card` subtracted one. PR #3 is **1-based**:
`validate_answer` accepts only `1 <= n <= len(options)`. The version
first committed would have sent "the first one" as `{"choice": 0}`,
which the real controller rejects. Fixed in `5b01f34`. The stub now
validates exactly as PR #3 does, and a regression test pins
`{"choice": 0}` as rejected. The other assumption held: `answer()` runs
callbacks synchronously on the answering thread, after its lock is
released.

## Live results and cost

| | Call 1 | Call 2 |
|---|---|---|
| Stream open after dial | 14.9 s | 14.5 s |
| Ended by | far end | **us** (hold → `complete_call`) |
| Status / duration | completed / 18 s | completed / 31 s |
| Frames out / in | not captured (script bug, fixed) | 1392 / 1436 |
| Rated price | not yet rated | not yet rated |

**Pricing** (Twilio's public pages, checked 2026-09-25):

| Line item | Price |
|---|---|
| Outbound to US | $0.0140/min |
| Outbound to Singapore, landline | $0.0423/min |
| Outbound to Singapore, mobile | $0.0578/min |
| Media Streams | $0.0044/min |

A call costs about **$0.0184/min to a US number and about $0.062/min
to a Singapore mobile**, 3.4× as much. Billing rounds up to whole
minutes.

What changes for a Singapore *from* number: renting one is listed from
$1.15/mo for international numbers, and Singapore numbers typically
need address/regulatory documents before purchase. Today's from-number
is US, which reaches SG fine but shows her family a foreign caller ID.

Twilio had not rated either call when last fetched, about 10 min after.
Rating is asynchronous; fetch `Calls/{sid}.json` later for `price`.

## What could not be verified

- **Live-mic clarity to the far end.** On call 1 the raw mic was muted
  in Pulse. On call 2 the far end heard *acoustic feedback*: laptop mic
  → call → the phone's speaker beside the laptop → laptop mic.
  - It showed as a narrow 1995 Hz tone 61–65 dB above the median,
    pulsing every ~860 ms, starting when the injected sentence ended.
  - The tone is in the raw capture, before any of our processing. The
    phone's own echo canceller kept it out of the inbound track.
  - The box's mic gain made it worse (2.3% clipping on the raw mic,
    already in TODO.md).

  Not a code fault, but clear live voice from this box is not yet
  demonstrated.
- **AEC during a call.** Output was on headphones, so there was no
  acoustic echo to cancel. The 1.6 dB raw-vs-ec figure from call 2 is
  noise suppression on a noisy mic, not ERLE. What is verified is the
  wiring: the far end plays into the ec sink and our capture comes from
  the ec source. A real number needs a speaker and a clean mic (e.g. a
  USB mic), measured like `smoke.py --aec`.
- **Rated price.** See above.
- **The hold on real hardware.** The hold is `FakeHoldSeam` until
  PR #3's `HoldController` is wired (diff below).
- **Stages 2 and 3 live.** Tested against fakes only, by instruction.
  Nothing has been saved or matched through a real Whisper transcript
  yet.

## Debt

- **Howl guard, not built (not in scope).** A detector on the outbound
  path for a sustained narrow tone (like call 2's 2 kHz peak), with a
  gain limiter that ducks outbound for a moment. Its trigger in real
  use is someone's phone on speaker near the device.
- **No `X-Twilio-Signature` check on `/twiml`.** It needs the account
  auth token, which the device doesn't hold. Until the production relay
  checks it, the quick tunnel's random hostname is the only guard.
- **Quick-tunnel hostnames take 60–90 s to resolve** through this box's
  resolver (measured: 66 s, 67 s, 84 s). This is why
  `CallController.prepare()` belongs at boot, not on first dial.
- **Our test hang-up came without warning.** That only mattered for the
  test script. In the product the hang-up is hers to start, so there is
  nothing to announce.
- **`~/.saathi/env` has whitespace around `=` on some lines.** `.`/
  `source` then executes the values as commands, and the shell printed
  two phone numbers to the terminal (nowhere else). systemd's
  `EnvironmentFile=` tolerates it. The live script parses the file
  instead. Tidy the file: `KEY=value`, no spaces.
- **Thin threshold gaps** (0.925 vs 0.956; 0.783 vs 0.800). Retune from
  real misses by adding them to the corpus.
- **Zhou/Chou** (a Wade-Giles variant) is a non-match by design, because
  folding zh with ch would merge Zhang and Chang.
- **`answer_card` is calling's own voice-answer tool.** If SCREEN ships
  one, it should win.
- **Only the first tool call per turn is handled** (existing,
  DECISIONS 2026-09-18). Saving a number and calling in one sentence
  takes two turns.

## The production relay

The first always-on service this product has. It must be:

1. **A static hostname with TLS**, e.g. `relay.<domain>`. Twilio needs
   `https://…/twiml` and `wss://…/media` on 443. A *named* cloudflared
   tunnel (`cloudflared tunnel create` + DNS route) gives this with no
   inbound port on her router. A small VPS running the same aiohttp app
   behind Caddy is the alternative.
2. **Twilio request-signature validation** on `/twiml` (HMAC-SHA1 of
   the URL + POST params with the account auth token, checked against
   `X-Twilio-Signature`). Reject unsigned requests. `/media` should
   accept only a `streamSid`/`callSid` the device just placed; pass a
   per-call token as a `<Parameter>` in the TwiML and check it on
   `start.customParameters`.
3. **A restart policy.** A systemd unit on the precedent of
   `saathi-engine.service` in `scripts/setup-pi.sh`: `Restart=always`,
   `RestartSec=2`, `After=network-online.target time-sync.target`, plus
   a health check on `/healthz`.
4. **Secrets from `/etc/saathi/env`** (mode 0600, root-owned), never
   the repo. `TWILIO_*` stays on the device only if the device places
   calls; the auth token for signature checks lives only on the relay.
5. **Replaceable.** It implements `Relay` (`public_url` = the static
   URL; `start`/`stop` no-ops or a health probe). Nothing else changes.

`setup-pi.sh` says of itself "written and reviewed, never run". The
relay unit would be the first unit actually run.

## Proposed: a call state in `core.py`

Not built: `core.py` and `Face` are shared, and `Face` is one of the
five interfaces.

- **State:** `IN_CALL = "in_call"`. Not `HANDOFF`: SPEC defines that as
  the slower-path question resolving back into the same turn.
- **Transitions:**
  - `(_ANY, "call_started") -> IN_CALL`, fired by `CallController` when
    its state goes to DIALLING.
  - `(IN_CALL, "call_ended") -> IDLE`, fired when it returns to IDLE.
  - `press`/`release` have no edge from `IN_CALL`, so a tap does
    nothing. The hold seam is what hangs up.
- **Face:** eyes soft and attentive to one side, "listening to someone
  else". Blinks continue, gaze drift stops, and one ambient cue shows
  the line is open. No status text.
- **Initiative:** `policy.py`'s busy gate (SPEC: "whether something
  else is happening — … a call"; DECISIONS: reminders "held one tick
  during a call") should read `core.state == IN_CALL`. Until the state
  exists it should read `CallController.active`.
- **Wiring:** `controller.on_state_change` →
  `loop.call_soon_threadsafe(core.handle, Event(...))`.

## Cross-territory edits needed

### `saathi/cli.py`: register, grant, thread ids, wire cards and hold

*Applied on `cloud/demo` 2026-09-26 as `_build_calling` in `cli.py`, with
two changes: `prepare()` runs on a background thread at boot (the tunnel
took ~84 s), and a missing credential/binary/pair or a failed tunnel
registers an "unavailable" `call_contact` instead of returning `None`.*

Assumes PR #3 is merged (`saathi/screen/cards.py`, and
`build_app(..., cards=, hold=)`); confirm the exact kwarg names against
PR #3.

```diff
@@ def _run() -> int:
         from saathi.tools.language import SET_LANGUAGE_DESCRIPTION, make_set_language_tool
         from saathi.tools.llm_schema import tool_to_openai_schema
         from saathi.tools.registry import PermissionDenied, Registry, UnknownTool
+        from saathi.screen.cards import CardController, HoldController
         from saathi.voice.engine.cascade import CascadeSession
         from saathi.voice.tts.registry import DEFAULT_BACKEND_ID
 
         registry = Registry()
         set_language_tool = make_set_language_tool(store)
         registry.register(set_language_tool)
-        granted_permissions = frozenset({"preferences"})
+        # Calls are in scope from 2026-09-25 (DECISIONS), reversing
+        # 2026-09-18. "contacts" writes her memory; "calls" rings a phone.
+        granted_permissions = frozenset({"preferences", "calls", "contacts"})
+        cards = CardController()
+        hold = HoldController(cards)
+        call_tools = []  # filled below once the echo-cancel pair is known
@@
         tool_schemas = [tool_to_openai_schema(set_language_tool, SET_LANGUAGE_DESCRIPTION)]
@@
             handles = ensure_echo_cancellation(mic.id, speaker.id)
             if isinstance(handles, EchoCancelHandles):
+                call_controller = _build_calling(store, handles, cards, hold, registry)
+                if call_controller is not None:
+                    tool_schemas.extend(call_controller.schemas)
                 session = CascadeSession(
@@
     run(
         core,
         config.screen_host,
         config.screen_port,
         session=session,
         capture_source_id=capture_source_id,
         store=store,
+        cards=cards,
+        hold=hold,
     )
     return 0
+
+
+def _build_calling(store, handles, cards, hold, registry):
+    """Calling, if Twilio is configured. The echo-cancel ids come from
+    aec.py here; nothing in saathi/call/ names a device."""
+    from saathi.call.audio import CallAudioBridge
+    from saathi.call.choosing import ChoiceFlow
+    from saathi.call.controller import CallController
+    from saathi.call.media import DEFAULT_PORT, MediaServer
+    from saathi.call.phone import infer_country
+    from saathi.call.relay import CloudflaredQuickTunnel
+    from saathi.call.saving import SaveFlow
+    from saathi.call.twilio import RestTwilioClient, TwilioCredentials
+    from saathi.identity.preferences import LANGUAGE_KEY, read_preference, write_preference
+    from saathi.identity.store import IdentityStore
+    from saathi.tools.calling import (
+        ANSWER_CARD_DESCRIPTION, CALL_CONTACT_DESCRIPTION, SAVE_CONTACT_DESCRIPTION,
+        make_answer_card_tool, make_call_tool, make_contact_dialer, make_save_contact_tool,
+    )
+    from saathi.tools.llm_schema import tool_to_openai_schema
+
+    creds = TwilioCredentials.from_env()
+    if creds is None:
+        print("Calling off: missing " + ", ".join(TwilioCredentials.missing_names()))
+        return None
+    path = store.path
+
+    def locale():
+        with IdentityStore(path) as own:  # handler threads: own connection
+            return infer_country(
+                read_preference(own, "country"), language=read_preference(own, LANGUAGE_KEY)
+            )[0]
+
+    def learn_country(iso):
+        with IdentityStore(path) as own:
+            write_preference(own, "country", iso)
+
+    tunnel = CloudflaredQuickTunnel(DEFAULT_PORT)
+    controller = CallController(
+        creds, RestTwilioClient(creds), tunnel, None,
+        lambda: CallAudioBridge(handles.source_id, handles.sink_id), hold,
+    )
+    controller._server = MediaServer(controller, controller.ws_url, port=DEFAULT_PORT)
+    controller.prepare()  # a quick tunnel needs 60-90 s to resolve; start it at boot
+    saves = SaveFlow(path, cards, locale, on_country_confirmed=learn_country)
+    choices = ChoiceFlow(cards, make_contact_dialer(controller))
+    tools = [
+        (make_call_tool(controller, path, choices), CALL_CONTACT_DESCRIPTION),
+        (make_save_contact_tool(saves), SAVE_CONTACT_DESCRIPTION),
+        # Drop this one if SCREEN ships a voice-answer path of its own.
+        (make_answer_card_tool(cards, [saves, choices]), ANSWER_CARD_DESCRIPTION),
+    ]
+    for tool, _ in tools:
+        registry.register(tool)
+    controller.schemas = [tool_to_openai_schema(t, d) for t, d in tools]
+    return controller
```

Two follow-ups are worth doing when this lands:

- Give `CallController` a `server` constructor argument that accepts a
  factory taking the controller, so the `_server` assignment goes away.
- Add `shutdown()` on exit.

### `saathi/audio/playback.py`: `play_stream()`

Move `PcmSinkWriter` here as the general streaming primitive, then
import it from `call/audio.py`.

```diff
+class StreamHandle:
+    """Raw PCM16 mono into a sink via `pacat`'s stdin. `write()` returns
+    False once the child is gone; `close()` drains and reaps it."""
+    # body: saathi/call/audio.py PcmSinkWriter, unchanged
+
+
+def play_stream(sink_id: str, sample_rate: int, latency_ms: int = 60) -> StreamHandle:
+    handle = StreamHandle(sink_id, sample_rate, latency_ms)
+    handle.open()
+    return handle
```

### `saathi/audio/aec.py`: make the echo-cancel pair the default

Pulse's default sink on this box is the raw one. Anything that plays
without naming a sink (the browser's audio, a system sound) bypasses
the canceller and becomes echo on a call.

```diff
 def ensure_echo_cancellation(
-    mic_id: str, speaker_id: str, system: SystemEchoCancel | None = None
+    mic_id: str, speaker_id: str, system: SystemEchoCancel | None = None,
+    make_default: bool = True,
 ) -> EchoCancelHandles | WebrtcAec:
     system = system or SystemEchoCancel()
     try:
-        return system.ensure(mic_id, speaker_id)
+        handles = system.ensure(mic_id, speaker_id)
+        if make_default:
+            system.make_default(handles)
+        return handles
     except (subprocess.SubprocessError, FileNotFoundError, RuntimeError):
         return WebrtcAec()
+
+# in SystemEchoCancel:
+    def make_default(self, handles: EchoCancelHandles) -> None:
+        self._run(["set-default-sink", handles.sink_id])
+        self._run(["set-default-source", handles.source_id])
```

### `saathi/call/cards.py` → PR #3's `saathi/screen/cards.py`

At merge, replace `from saathi.call.cards import …` with
`from saathi.screen.cards import …` in these files:

- `call/saving.py`, `call/choosing.py`, `tools/calling.py`
- `tests/test_call_saving.py`, `tests/test_tools_calling.py`

Then delete `call/cards.py`. For tests, keep `FakeCardController` in a
test helper, or use PR #3's real controller directly.

The shapes used are `choice`/`confirm`/`readback`, `TooManyOptions`,
`show`/`clear`/`answer`/`on_answer`, and `Answer`, with Choice answers
1-based.

`HoldSeam` matches `HoldController.set_handler(on_complete, *,
seconds=2.0, label=…)` and `clear()`. Replace `FakeHoldSeam` with
`HoldController(cards)` in `cli.py`.

## Proposed SPEC.md diffs (not applied)

```diff
 ## Scope
 
 **In:** conversation (two engines, one interface), the face, peripheral
 detection, local AEC, persistent memory, learned personality, proactive
-check-ins, spacebar push-to-talk, tool registry.
+check-ins, spacebar push-to-talk, tool registry, phone calls (Twilio
+Media Streams through a public relay) to contacts held in memory.
 
-**Out:** calls, music. Their **tool stubs exist** in v1 so v2 swaps an
-implementation rather than inventing plumbing.
+**Out:** music. Its **tool stub exists** so a later version swaps an
+implementation rather than inventing plumbing — as `call_contact` did.
```

```diff
 ## State machine
 
 `SLEEPING -> IDLE -> ATTENTIVE -> LISTENING -> THINKING -> SPEAKING`, plus
-`HANDOFF` when a question goes to the slower, smarter path.
+`HANDOFF` when a question goes to the slower, smarter path, and `IN_CALL`
+while a phone call is open. A tap does nothing in a call; a two-second
+spacebar hold hangs up.
```

```diff
 ## Memory
+
+A person she can phone is an `entities` row (`kind="person"`, the number
+as JSON in `notes`); relationships are `edges` from her own `kind="self"`
+row. No contacts table. A corrected number is a new row; reads take the
+latest per name.
```

```diff
+## Cards
+
+A card is shown, never awaited inside a turn. The tool that shows it
+returns the card's spoken text; her answer — tap or voice — arrives
+separately. A number is read back digit by digit before it is saved;
+an inferred country code is said aloud. A name that could be two
+people is a question, never a dial.
```
