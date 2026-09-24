# Completed work — index

One report per item, what was built/decided/verified/left open in each.
Read `DECISIONS.md` (repo root) for the retroactive, one-line-each
record of every substitution and judgment call across all of them.

- [A — Robustness](A-robustness.md)
- [B — Barge-in](B-barge-in.md)
- [C — Five TTS backends](C-tts-backends.md)
- [D — Model bake-off](D-model-bakeoff.md)
- [E — Instrumentation](E-instrumentation.md)
- [F — Memory](F-memory.md)
- [G — Language switching](G-language.md)
- [Latency investigation](latency-investigation.md) — real network/
  encoding/EOU measurement, triggered by a real over-budget sample
- [Checkpoint 3](checkpoint-3.md) — `reflect.py`, `profile.py`,
  `initiative/scheduler.py` + `policy.py`, started, not wired to speak
- [Screen](screen.md) — accessibility cards (the module other streams
  import) + the YouTube panel beside the face; the AEC finding; the
  `cli.py`/`audio/aec.py` diffs still to apply

## Google TTS — exactly what's needed when you're back

Left alone this session, on purpose: still no GCP credentials
configured anywhere on this machine. Both Google backends
(`GoogleNeural2WaveNetBackend`, `GoogleChirp3HDBackend`) are real, real
streaming-API code, and report themselves honestly unavailable in the
Ctrl+L panel until this is done — nothing else depends on it.

1. In a GCP project: enable `texttospeech.googleapis.com`.
2. Create a service account, grant it `roles/cloudtexttospeech.client`
   — flagged in earlier chat as convention-based, not independently
   confirmed against a first-party role-reference page. If that exact
   role name doesn't exist when you go looking, a scoped custom role
   limited to `texttospeech.*.synthesize` is the documented fallback.
3. Download that service account's JSON key.
4. Either:
   - Run `scripts/setup-pi.sh` (or re-run it — it's idempotent) and
     answer yes to the optional GCP prompt, pointing it at the
     downloaded key file; it installs it to `/etc/saathi/gcp.json`
     (mode 0600, root-owned) and wires
     `GOOGLE_APPLICATION_CREDENTIALS` into `/etc/saathi/env`, **or**
   - By hand: copy the key to `/etc/saathi/gcp.json` (`chmod 600`,
     root-owned), add `GOOGLE_APPLICATION_CREDENTIALS=/etc/saathi/gcp.json`
     to `/etc/saathi/env`, then `sudo systemctl restart saathi-engine`.
5. `google-cloud-texttospeech` isn't installed by default — install it
   with `uv sync --extra google-tts` (or `--all-extras`) before the
   backends' `available()` will report `True`.

Once that's done, item C's 5-backend comparison should be re-run for
real — everything currently in `docs/completed/C-tts-backends.md` about
Google is "should work, never tested," not "verified."
