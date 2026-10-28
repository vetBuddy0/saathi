# Running and demoing Saathi (cloud/demo, 2026-09-25)

What this branch does today, and how to drive it. Known gaps are at the
end — read them before a demo.

## Setup (once)

```sh
git checkout cloud/demo && git pull
uv sync --group dev --group google-tts   # plain `--group dev` REMOVES Google TTS
```

`~/.saathi/env` (mode 0600, never in the repo) needs:

| Variable | For |
|---|---|
| `OPENAI_API_KEY` | speech-to-text + replies (used whenever it is set) |
| `GROQ_API_KEY` | fallback provider, and `saathi smoke` if OpenAI isn't set |
| `GOOGLE_APPLICATION_CREDENTIALS` | the Chirp voice |
| `YOUTUBE_API_KEY` | music/video search |
| `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`, `TWILIO_FROM_NUMBER`, `TWILIO_TEST_NUMBER` | calling (plus `cloudflared` in `~/.local/bin`) |

The app does **not** read that file itself — load it into the shell:

```sh
set -a; . ~/.saathi/env; set +a
```

## Run

```sh
uv run saathi smoke          # mic/speaker found, AI key accepted
uv run saathi voice          # should say google-chirp3-hd, available now: yes
uv run saathi run            # face on http://127.0.0.1:8765
```

Open the page in Chrome (or `scripts/saathi-face-kiosk.sh`). Tests:
`env PYTHONPATH= uv run pytest -q` (PYTHONPATH must be cleared on this
laptop — ROS plugins break pytest otherwise).

**Phone as mic and speaker (2026-10-08):** the Android shell connects to
`ws://<laptop>:8765/audio` as well as `/ws`. While it is connected, a
press takes the microphone from the phone and every reply sentence plays
on the phone, not the laptop; disconnect it and the laptop's parec/paplay
path is back at the next press. The protocol both sides implement is in
`saathi/screen/server.py`'s docstring; the engine side is
`saathi/audio/remote.py`.

**Mic:** if every press ends silently, the input has probably switched to
a headset jack with no mic:

```sh
pactl set-source-port alsa_input.pci-0000_00_1f.3.analog-stereo analog-input-internal-mic
```

If presses end silently on the right port, the mic may be clipping: on
the 800-series laptop, 100% input volume is +60 dB over base and ~40% of
samples hit full scale (2026-10-07). 15% gave a clean signal:

```sh
pactl set-source-volume @DEFAULT_SOURCE@ 15%
```

`saathi smoke` needs `pactl` (`sudo apt install pulseaudio-utils`); without
it, it reports no mic and no speaker even when both work.

## Wake word

Say "Saathi" and it listens; no key needed. "Saathi, play a song" in one
breath works too. The name is heard on this device (faster-whisper
`tiny.en`, downloaded on first run); `SAATHI_WAKE_WORD=off` turns it off.

Every turn needs the name (or the spacebar): after a reply it goes back
to idle, so a TV or video in the room isn't answered.
`SAATHI_OPEN_CONVERSATION=on` keeps the mic open 7 s after each reply.

## AI provider

OpenAI whenever `OPENAI_API_KEY` is set: `gpt-transcribe` hears her,
`gpt-4.1` replies (both chosen by measurement — DECISIONS.md). Override
without code: `SAATHI_AI_PROVIDER=groq|openai`, `SAATHI_LLM_MODEL=…`,
`SAATHI_STT_MODEL=…`. She starts speaking ~2 s after the spacebar is
released (Groq was ~1.3 s but rate-limited; see TODO.md).

## Ctrl+L panel

- **Language** she replies in.
- **Voice** — Chirp (warm, same speaker in English and Mandarin) or
  Piper (offline fallback).
- **Captions** — show/hide a strip with what it *heard* and what it
  *said*. Turn this on for demos: it tells a bad mic from a bad reply.

## Developer console

Memory, personality, contacts and keys, for whoever sets Saathi up (not
her screen). Laptop: open `http://127.0.0.1:8765/admin` on the same
machine. Tablet: hold the face 5 s → setup dialog → **Developer console**.
"How it works" shows one turn end to end; "Personality" shows the exact
text the model receives. Edits apply from the next turn.

## What to say

Hold nothing — tap space, speak, and it answers.

**Commands that work instantly** (no model in the loop; `voice/router.py`)
- Once a search has shown titles: "pause", "hold on", "carry on",
  "stop", "close YouTube", "volume up" / "down", "make it bigger" /
  "smaller", "another one", "play it again", "never mind", "the second
  one" / "number two" / "two".
- Always: "call my son", "ring Priya", "call the test number".
- While a calling card is up: "yes", "no", "the first one".
- Say the command as its own sentence; a trailing "please" is fine. If
  it isn't matched it simply goes to the model as before. Turn the
  router off with `SAATHI_COMMAND_ROUTER=off` to compare.

**Music / video** (YouTube, in a panel beside the face)
- "Play some old Chinese songs" / "play Ed Sheeran Perfect" → a card with
  up to three titles, read aloud. Tap one or say "the second one".
- "Another one" (next result), "something else" (new search)
- "Pause", "carry on", "volume up" / "volume down"
- "Make it bigger" / "smaller" (full screen and back)
- "Stop" / "close YouTube" — stops and hides the panel
- Tapping space while a video plays ducks it; it comes back when she's
  done speaking.

**Memory**
- "No, that's wrong …" / "forget that" — retires the belief and records
  the correction.

**Calling** (ready ~90 s after `saathi run` starts — the tunnel)
- "Call my son" — rings the saved son (currently +65 …423).
- "Call the test number"
- Hold space 2 s to hang up; a short tap during a call does nothing; an
  unanswered call clears itself after 45 s.
- Keep the phone away from the laptop, or it howls.

**Calling on the Android app through Twilio (2026-10-28).** The face
page places the call itself (Twilio's Voice JS SDK, bundled), so no SIM,
no laptop and no tunnel. Twilio dials her son from your Twilio number.
Set up once in the Twilio console:
1. **TwiML Bin** (Develop -> TwiML Bins -> Create), friendly name
   `saathi-dial`, with your Twilio number as the caller ID:
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <Response>
     <Dial answerOnBridge="true" callerId="+1XXXXXXXXXX">
       <Number>{{To}}</Number>
     </Dial>
   </Response>
   ```
   Save, and copy its URL (`https://handler.twilio.com/twiml/EH...`).
   `answerOnBridge` is what makes the panel say Connected only when he
   actually picks up.
2. **TwiML App** (Voice -> TwiML Apps -> Create): Voice Request URL =
   the Bin's URL, method POST. Copy its SID (`AP...`).
3. On the tablet's keys page (long-press the face): the five `TWILIO_*`
   keys as for the laptop (an **API key** SID/secret, not the auth
   token), plus `TWILIO_TWIML_APP_SID` = the `AP...` SID. Save.
4. A **trial** Twilio account can only call numbers verified in the
   console (Phone Numbers -> Verified Caller IDs): verify the son's.
5. Allow the microphone when the app asks.
"Call my son" then: she hears "Calling Udhi.", the phone panel shows
Calling -> Ringing -> Connected with a timer, and the red End call button
(or holding the talk button 2 s) hangs up. Without `TWILIO_TWIML_APP_SID`
the app falls back to the SIM route below.

**Calling on the Android app (2026-10-28): the device's own SIM.**
With the engine inside the app, "call my son" rings a normal phone call
from the tablet's SIM, on speaker, after she hears "Calling Udhi.".
- Allow **Phone calls** when the app asks (with the microphone). If it
  was refused, the dialer opens with the number filled in instead -- one
  tap on the green button rings.
- The son has to be a saved contact on the tablet: say "save my son
  Udhi's number, eight nine six one ..." and answer the read-back card.
  "Call the test number" uses `TWILIO_TEST_NUMBER` from the keys page.
- Hang up on the phone's own in-call screen; the face comes back after.
- No SIM, the SIM not ready, or a data-only tablet (many LTE tablets
  can't make voice calls -- check the tablet's own Phone app can ring a
  number first): she says this device can't place calls.
- Stop any song first ("stop") -- whether YouTube pauses on its own for
  the call hasn't been checked on the tablet.

## Family app: free calls to a paired phone

Calls to family go over the internet (WebRTC), at no cost per minute.
Anyone paired is rung through the app; Twilio is used only for people
who aren't paired (DECISIONS 2026-10-08).

**Turn it on.** Add this to `~/.saathi/env` and restart `saathi run`:

```sh
SAATHI_FAMILY_APP=on
# Recommended: a permanent address (see "Stable URL" below)
# SAATHI_PUBLIC_URL=https://saathi.example.org
```

If `SAATHI_PUBLIC_URL` isn't set, a quick tunnel starts at boot. It is
reachable about a minute later, and its address changes every restart.

**Pair a phone** (Android Chrome, or iPhone Safari on iOS 16.4+):
1. On Saathi, press **Ctrl+P**. A QR code appears; it works once, for
   10 minutes. Esc closes it.
2. Scan it with the phone's camera and open the link.
3. Enter your name, your relationship to her, and what you call her
   ("Mum"). Tap **Pair this phone**.
4. Tap **Turn on call alerts** and allow notifications. Without this the
   phone can't ring.
5. Install the app. On Android: menu → *Install app* / *Add to Home
   screen*. On iPhone: Share → **Add to Home Screen**, then open Saathi
   from the home screen and do step 4 there. iOS only delivers
   notifications to installed web apps.

Ctrl+P also lists paired phones, each with **Unpair**.

**Use it**
- She says "call Priya" or "call my daughter". The phone shows
  "Saathi — Mum" with Answer and Decline buttons. The panel on the right
  of her screen shows the call and its End button; holding space for
  2 s also hangs up.
- From the app, **Call Mum** rings Saathi: a ring tone plays and a card
  says "Priya is calling. Answer?". She answers by tapping Yes or
  pressing space. "Saathi … yes" also works if the model calls
  `answer_card`. A call is never answered without her doing one of
  these. An unanswered ring stops after 45 s.

**Stable URL** (do this before pairing anyone for real). A quick
tunnel's address changes on every restart. An installed app keeps the
old address, so it stops being able to start calls. Rings still get
through, because each one carries the current address. To give Saathi a
permanent address, set up a named Cloudflare tunnel. This needs a free
Cloudflare account and a domain on Cloudflare:

```sh
cloudflared tunnel login
cloudflared tunnel create saathi
cloudflared tunnel route dns saathi saathi.example.org
cat > ~/.cloudflared/config.yml <<CFG
tunnel: saathi
credentials-file: /home/$USER/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: saathi.example.org
    service: http://127.0.0.1:8769
  - service: http_status:404
CFG
cloudflared tunnel run saathi        # or: sudo cloudflared service install
```

Then set `SAATHI_PUBLIC_URL=https://saathi.example.org`. Point the
tunnel only at port **8769** (the family app), never at 8765 (her
screen). `SAATHI_FAMILY_PORT` changes the port, and
`SAATHI_VAPID_SUBJECT` (e.g. `mailto:you@example.org`) is the contact
address push services see.

**TURN** (if calls ring but never connect). The default is free public
STUN. That works between most home networks, but some mobile carriers
need a TURN relay. A call that doesn't connect within 30 s ends, and
the log mentions TURN. Two options:
- Cloudflare Realtime TURN (has a free tier): in the Cloudflare
  dashboard, create a TURN key, then set
  `SAATHI_TURN_CLOUDFLARE_KEY_ID` and
  `SAATHI_TURN_CLOUDFLARE_API_TOKEN`. Short-lived credentials are made
  for each call.
- Any TURN server (e.g. coturn):
  `SAATHI_ICE_SERVERS='[{"urls":["stun:stun.cloudflare.com:3478"]},{"urls":"turn:turn.example.org:3478","username":"u","credential":"p"}]'`.

**Keys and data.** The device's push key is
`~/.saathi/vapid_private.pem`. Deleting it unpairs every phone's alerts.
Pairings are in `~/.saathi/family.sqlite3`.

## Known gaps (TODO.md has details)

- **Label uploads refuse the embed.** "Ed Sheeran - Perfect" and similar
  report embeddable through the API and still fail in the official
  player (error 150). The player reports it, and from then on that video
  is sent with `target: "browser"` — the real youtube.com page, which
  only the Android shell's YouTube pane can show (DECISIONS.md
  2026-10-08). On a laptop with just the face page there is no taker,
  so the refusal is treated as the browser's would be: the video is
  dropped for the session and the other results come back on a card.
  (The yt-dlp direct stream that covered this is gone: it breached
  YouTube's terms.)
- **Name matching** can dial a near-miss ("Deepa" → Deepak) and mixes up
  two people with the same name or relation (S2–S4). Stick to "call my
  son" / the test number in a demo.
- **Built-in mic is noisy** — speak close to the laptop; captions show
  what was heard.
- The per-turn cost isn't recorded for OpenAI yet.
