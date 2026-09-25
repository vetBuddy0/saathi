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

**Mic:** if every press ends silently, the input has probably switched to
a headset jack with no mic:

```sh
pactl set-source-port alsa_input.pci-0000_00_1f.3.analog-stereo analog-input-internal-mic
```

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

## Known gaps (TODO.md has details)

- **YouTube playback is direct (demo only).** `SAATHI_YOUTUBE_PLAYBACK=direct`
  in `~/.saathi/env` plays via yt-dlp, because label uploads ("Ed Sheeran -
  Perfect") refuse the official embed (error 150). This breaches YouTube's
  terms and must not ship; remove the line for the official player. A
  pick takes ~3 s to start while the stream is looked up.
- **Name matching** can dial a near-miss ("Deepa" → Deepak) and mixes up
  two people with the same name or relation (S2–S4). Stick to "call my
  son" / the test number in a demo.
- **Built-in mic is noisy** — speak close to the laptop; captions show
  what was heard.
- The per-turn cost isn't recorded for OpenAI yet.
