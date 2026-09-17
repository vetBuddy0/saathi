# Saathi — v1 spec

A companion device for an elderly person living alone. Talks, remembers, and
starts conversations rather than only answering them.

This is the contract. Where a task conflicts with it, say so.

---

## The rule everything follows

Saathi is a persistent character with interchangeable parts underneath. The
voice model, reasoning model, transport and hardware are all replaceable
because identity lives in a file above them.

So: **the engine performs the character, it does not hold it.** The core
compiles context between turns and hands it over.

---

## Scope

**In:** conversation (two engines, one interface), the face, peripheral
detection, local AEC, persistent memory, learned personality, proactive
check-ins, spacebar push-to-talk, tool registry.

**Out:** calls, music. Their **tool stubs exist** in v1 so v2 swaps an
implementation rather than inventing plumbing.

**Never:** agent framework, vector DB server, message bus, containers, desktop
environment, device names in code.

---

## Architecture

One process we write, one browser, one system service.

```
PipeWire (AEC) -> saathi-core -> WebSocket -> Chromium kiosk (face)
                              -> WebSocket -> voice engine (remote)
```

```
saathi/
  core.py              state machine; the only thing that sets state
  config.py

  audio/
    devices.py         enumerate, choose, hotplug
    aec.py             PipeWire module-echo-cancel; pywebrtc-audio fallback
    capture.py         clean mic -> frames
    playback.py
    wake.py            openWakeWord
    vad.py             Silero

  voice/
    session.py         start, send_audio, say, on_audio, on_intent, interrupt
    transport/         websocket.py (start here) | local.py | livekit.py (later)
    engine/            realtime.py | cascade.py

  identity/
    store.py           SQLite
    compile.py         state -> context block
    reflect.py         episodes -> rules
    profile.py         family-visible view

  tools/
    registry.py        name -> schema + permission + callable
    builtin.py         remember, recall, time, set_reminder
    stubs.py           call_contact, play_music

  initiative/
    scheduler.py       when to speak first
    policy.py          when to stay quiet

  screen/
    server.py          serves the page, holds the WebSocket
    static/

  smoke.py
  cli.py
```

### Two non-obvious flows

**The face is driven by `core.py`, never by the engine.** That is why it can
react in 100 ms — core knows there is audio energy long before a model has an
opinion. The engine may emit `presence: attentive`; the renderer decides what
that looks like.

**Context is compiled between turns, never during one.** The session already
holds it when she starts speaking; the core updates it afterwards. Memory is
one turn stale on purpose — compiling in the hot path spends the latency we
are protecting.

---

## The five interfaces

Changing one of these is a conversation, not a commit.

| | v1 implementations |
|---|---|
| `VoiceSession` | realtime, cascade |
| `Transport` | websocket, local |
| `Tool` | builtin, stubs |
| `Face` | eyes, orb, ink |
| `IdentityStore` | sqlite |

The voice engine never executes anything. It emits intent; the core validates;
the tool executes.

---

## Memory

One SQLite file on the device. Not a service — the identity file is the
product's asset and lives where the device is.

```sql
entities(id, kind, name, notes, created_at)
edges(src, dst, relation, since, until)          -- graph, via recursive CTE
episodes(id, ts, entity_id, text, importance, embedding)
rules(id, text, confidence, learned_at, source_episode, active)
preferences(key, value, updated_at)
reminders(id, due_at, text, recurrence, active)
turns(id, ts, mode, eou_ms, engine_ms, first_audio_ms, handoff, engine)
initiatives(id, ts, kind, reason, source_episode, spoken, suppressed_by)
```

Vector search via `sqlite-vec`. At a few thousand episodes, brute-force cosine
in numpy is also fine.

**Retrieval scores recency, importance and relevance combined** — follow
*Generative Agents* (Park et al. 2023). Similarity-only retrieval degrades as
history grows, which is the wrong failure curve for a two-year relationship.

**Stored and sent are different.** Stored: rows, confidence, provenance. Sent:
plain sentences — *"She likes being greeted by name. Don't ask how she slept."*
Models act on sentences and ignore floats. `compile.py` does that translation.

**Every rule records where it came from**, and `profile.py` exposes the set
readable and editable. A companion that learns something wrong about someone's
mother with no way to correct it is a support call nobody can answer.

---

## The face

**Eyes, no mouth.** An orb cannot look at someone, and looking at someone is
the signal for *I am listening to you*. A mouth demands lip-sync and bad
lip-sync is worse than none. Eyebrows read as children's illustration, which
lands as patronising.

Four cues: gaze toward her when she speaks; irregular blinking; looking away
while thinking; narrowing at the corners for warmth.

Eyes are a from-scratch reimplementation of
[RoboEyes](https://github.com/FluxGarage/RoboEyes)' animation model —
studied for its geometry and timing, never copied (RoboEyes is GPL-3.0;
nothing GPL enters this repo). Per-eye width/height/borderRadius and a
shared spaceBetween, everything else derived from those four; moods
DEFAULT/TIRED/ANGRY/HAPPY as eyelid geometry; blink, autoblinker, idle-mode
gaze drift, confused and laugh macros. (Supersedes the original plan to
vendor Web-Eye-Animation and port RoboEyes as loose ideas on top of it —
"exactly like RoboEyes" turned out to mean the model itself, not a
different library wearing its timing.) Render warm and soft: a warm dark
ground, not OLED black, eyes in warm gradients with glow, eased
continuously. Flat colour on pure black is a hardware limit, not a style.

**No status text under the face.** A person doesn't display a status label.
State lives in the eyes plus one ambient light cue. On-screen text is for
content — time, name, reminder — never status. If thinking exceeds ~1.5 s, say
something short *out loud*; silence reads as broken and a spinner isn't human.

**Three faces behind one interface.** Each is a few hundred lines. They exist
so a real person can pick — that decision isn't ours to make from intuition.

---

## State machine

`SLEEPING -> IDLE -> ATTENTIVE -> LISTENING -> THINKING -> SPEAKING`, plus
`HANDOFF` when a question goes to the slower, smarter path.

**After Saathi speaks unprompted, the mic opens for a window without a button
press.** Nobody presses a button to answer someone who spoke to them. This is
only safe because AEC exists, and it is why `VoiceSession` has `say()` — every
other entry point assumes she spoke first.

---

## Triggers

**Spacebar** is primary in v1 (stands in for the wearable button). Once a
conversation is open, follow-ups need no trigger — AEC is what makes an open
mic safe. **openWakeWord** is the across-the-room fallback.

Local wake word is the privacy architecture, not an optimisation: nothing
leaves the device until she asks for it. That sentence is what a care facility
needs to hear.

---

## Initiative

Three kinds, and the third is the product:

| Kind | Triggered by |
|---|---|
| Scheduled | Time — reminders, morning greeting |
| Event | Sensor — she arrived, a call came |
| **Noticed** | **Memory — "you said the scan was today"** |

The third is why reflection and initiative are the same checkpoint: reflection
writes *she has a scan Thursday and is anxious*, and the scheduler queries for
things worth following up that haven't been.

**`policy.py` needs a reason to speak, not the absence of a reason to stay
quiet.** Restraint is harder to build than initiative and nobody budgets for
it. A device that comments on everything is exhausting to live with, and that
failure is invisible in a twenty-minute test.

The gate reads presence, quiet hours, and whether something else is happening —
television, a call. Presence stops being optional decoration once initiative is
real.

**Every proactive utterance records why it fired.** Otherwise "why did it say
that" is undebuggable, and an annoying pattern is indistinguishable from a
useful one.

---

## Budgets

| Clock | Budget |
|---|---|
| Face reacts | 100 ms |
| Voice starts | 500 ms (speech-to-speech) / 1,200 ms (cascade) |
| Brain finishes | 3 s — hidden behind speech |

**A test reads the `turns` table and fails the build when a turn exceeds
budget.** Speed is a feature; it gets a regression test. It asserts on the
95th percentile, not the mean — a companion device's bad turns are the ones
a person notices, and a mean hides exactly those.

---

## Audio

**Local AEC is mandatory.** It fixes the device transcribing its own voice, and
it is what lets the mic stay open mid-conversation.

Try [PipeWire `module-echo-cancel`](https://pipewire.pages.freedesktop.org/pipewire/page_module_echo_cancel.html)
first — it routes the reference signal below the application, which is the part
that is actually hard. Fall back to
[pywebrtc-audio](https://github.com/strands-labs/pywebrtc-audio) (Apache 2.0,
aarch64 wheels).

**Bench test:** play a known tone, capture, assert residual below threshold.

---

## Peripherals

**No device name anywhere in code or config.** A card number written into a
file changes when something is unplugged; that cost more time than any other
bug in the previous build.

Enumerate at boot and on hotplug. Prefer USB capture over onboard; prefer a
device that has produced non-silent audio before; remember what worked.
Degrade rather than die — no camera means presence detection off, no screen
means voice only. Report missing hardware **on the screen**, in words her
family could act on.

`saathi smoke` exercises real hardware in 30 seconds and exits non-zero when
something critical is down. It gates every deploy.

---

## Tests

- CI on **x86 and arm64**. A missing ARM wheel must fail a build, not a person
  standing next to a Pi.
- **Undefined-name check** over the package — a rename that missed one call
  site already shipped a crash on this project.
- **Latency budget test**, from the `turns` table — 95th percentile, not
  the mean.
- **AEC bench**, as above.
- Audio, camera and screen behind interfaces with fakes, so the suite runs
  headless.
- `uv` pins the interpreter. Distro Pythons differ and that difference broke
  the last build.

**If a test looks wrong, say so. Do not change it to pass.**

---

## Checkpoints

| # | Done when |
|---|---|
| 1 | Spacebar pressed, face reacts in under 100 ms. `saathi smoke` correct. No AI. |
| 2 | Talk on either engine, recognisably the same character. Turns logged, within budget. |
| 3 | Talk, return a week later, it knows what mattered. |

---

## Tone

She is in her seventies, possibly alone, and did not grow up with computers.
Not a user, not a support call.

Never open with "Sure!" or "Certainly". Never close with "anything else I can
help with?". Never say "you already told me" — people repeat their stories and
that is not an error to correct.
