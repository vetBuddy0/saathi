# C — Five TTS backends

## What was built

`saathi/voice/tts/`: `TTSBackend` ABC (`synthesize_stream()` yields one
WAV per sentence — see B for why that shape exists), `split_into_sentences()`,
and five real backend implementations:

- **`PiperBackend`** — real, MIT, the last-resort offline fallback,
  kept wired regardless of which backend wins any comparison. Found and
  fixed a real bug in its voice-download path: it shelled out to a bare
  `"python3"`, which on this machine resolved to an unrelated Anaconda
  interpreter with no `piper` installed — switched to `sys.executable`.
- **`KokoroBackend`** — real, Apache 2.0, a genuine `manylinux_2_28_aarch64`
  wheel confirmed (454MB, `torch` unconditional). English works;
  Mandarin needs an additional dependency (`ordered_set`, likely via a
  `misaki[zh]` extra) that plain `pip install kokoro` doesn't pull in —
  attempted, didn't finish installing in 90s, left unresolved rather
  than chased further (see "Debt" below).
- **`MeloTTSBackend`** — confirmed genuinely uninstallable, not just
  slow: no wheels at all on PyPI (sdist only), and its pinned
  `torch<2.0` has zero Python 3.12 wheels on any platform, checked
  across three torch versions. Kept as a real `TTSBackend` entry
  (`available()` always `False` with this exact reason) so it stays
  visible in comparisons instead of silently missing.
- **`GoogleNeural2WaveNetBackend`** / **`GoogleChirp3HDBackend`** — real
  streaming-synthesis-API code, `asia-southeast1` endpoint, credential-gated
  `available()`. **Never tested against a real Google account** — no
  GCP credentials configured on this machine. The exact setup answer
  (API: `texttospeech.googleapis.com`; IAM role:
  `roles/cloudtexttospeech.client`, flagged as convention-based, not
  independently confirmed against a first-party role reference) was
  given in chat early so it could be set up in parallel; still
  outstanding.

Also built: the Ctrl+L settings panel (language + TTS backend
selection), GCP credential setup in `scripts/setup-pi.sh` (optional,
skippable, never blocks the rest of setup).

## Verified

- Real synthesis timing, all backends that can run here: Piper
  (English + Mandarin, both real), Kokoro (English real; Mandarin
  blocked — see above).
- The settings panel over a real headless-Chrome/CDP session: real
  `Input.dispatchKeyEvent` for Ctrl+L, a real DOM click on "chinese,"
  the round trip through `set_preference` → `IdentityStore` →
  broadcast `settings` update → the button showing selected, Escape
  closing it.
- Real audio samples produced and left on disk for listening (see the
  session's chat log for the exact scratch path — not committed to the
  repo; audio artifacts don't belong in source control).

## Debt / not verified

- **Google backends: zero real-world verification.** Credentials still
  not configured — see the session's final report for exactly what's
  needed.
- **Kokoro's Mandarin dependency gap** is unresolved, not silently
  worked around.
- **No Pi-side numbers** for any backend — this comparison only ran on
  the development laptop; item C's original brief asked for Pi numbers
  too, and no Pi was reachable from this environment.
- The cost panel (per-backend $/turn shown in the settings UI) needs
  item E's turns-table character-volume columns, which now exist
  (`prompt_tokens`/`completion_tokens`/`cost_usd`), but the panel itself
  was never wired to read and display them — a real follow-up, not
  started.
