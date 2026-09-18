# E — Instrumentation

## What was built

- `cascade.py`'s `TurnTimings` — `stt_ms`, `first_token_ms` (honestly
  labeled: the whole non-streamed completion call, not a true
  first-token measurement — see below), `first_tts_chunk_ms`,
  `prompt_tokens`, `completion_tokens`, `cost_usd`. Captured for real
  during `end_turn()`/`_speak()`, exposed via `pop_last_turn_timings()`
  — additive, not a `VoiceSession` Protocol change (same `getattr`
  pattern already used for `preload()`).
- `screen/server.py`'s `_log_turn()` — real turns are now actually
  logged to the `turns` table; before this, the schema existed and
  nothing wrote to it. A superseded (barged-into) turn correctly logs
  nothing.
- `saathi/latency_budget.py` — SPEC.md's "reads the turns table, fails
  the build on the 95th percentile, not the mean" as real, tested logic.
  10 tests, including one proving a single outlier among 20 turns
  correctly does *not* trip p95 while two do — the real, verified shape
  of nearest-rank percentile math, not assumed.
- **Schema migration** (originally proposed as a diff, later explicitly
  authorized and applied — see `DECISIONS.md`): `turns.engine_ms`/
  `first_audio_ms` split into `stt_ms`, `first_token_ms`,
  `first_tts_chunk_ms`, plus `prompt_tokens`/`completion_tokens`/
  `cost_usd`. Existing rows migrated in place
  (`identity/store.py`'s `_migrate_turns`), not dropped — verified
  against a real hand-built old-schema database, not just new-schema
  fixtures.

## Verified

- A real turn end-to-end (real Whisper, real Groq chat, real Piper) —
  confirmed the instrumentation captures real, sensible numbers.
- The full latency investigation (see
  `docs/completed/latency-investigation.md`) ran entirely on this
  instrumentation: 10 real trials each of two configurations, logged
  into real `IdentityStore` files, read back through the real
  `latency_budget.py` percentile logic.

## Decided rather than told

- `turns.mode` hardcoded to `"voice"` — the only mode this device has;
  SPEC names no other.
- Cost tracking (`cost_usd`) is LLM-only — doesn't include Whisper STT
  or TTS backend cost, which are priced in different units and tracked
  separately (`voice/tts/*_backend.py`'s own `cost_per_million_chars_usd()`).

## Not done / left open

- **`first_token_ms` is not a true streaming measurement.** Making it
  real needs `end_turn()` itself to stream the chat completion, which
  changes `VoiceSession`'s contract — a protected-interface decision,
  explicitly not made unilaterally this session.
- The settings panel's cost display (item C) still isn't wired to read
  these new columns.
- No automated deploy-time gate reads a real device's `turns` table yet
  — `latency_budget.py` is tested against synthetic data in CI (there's
  no real device in a GitHub Actions runner); a real gate against a
  real device's history is a separate, manual/future step.
