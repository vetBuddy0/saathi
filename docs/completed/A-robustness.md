# A — Robustness

## What was built

- `screen/static/js/main.js`: WebSocket reconnect with exponential
  backoff (500ms base, 30s cap). Verified live against a real
  server kill/restart cycle over a real headless-Chrome/CDP session
  (`Runtime.consoleAPICalled` events watched directly, not inferred).
- `screen/server.py`: an upstream Groq failure (401/429/timeout/
  connection reset) no longer crashes the turn silently — it speaks a
  short, non-technical fallback ("I didn't quite catch that...") and
  returns to IDLE. The real HTTP status/exception still lands in
  `journalctl` via `logger.exception`, just never spoken to her.
- `smoke.py`: `_report_groq_key()` — one cheap `client.models.list()`
  call to catch a bad `GROQ_API_KEY` before deploy, not live (a bad key
  presents identically to a broken audio pipeline: THINKING that never
  leaves). Verified against both a real valid key and a deliberately
  invalid one.
- `scripts/setup-pi.sh`: `prompt_secret()` sanitizes and validates the
  pasted Groq key (rejects malformed values, re-prompts), with an
  explicit leak audit in its own comments (never passed to `log`/`die`,
  no `set -x`). Traced to a real incident: a real Pi install once
  produced a 174-character value from a 56-character key; root cause
  not reproducible, so this defends rather than claims to have fixed
  the actual cause.

## Decided rather than told

- `core.handle()` changed from returning `State` to returning `bool`
  (transitioned or not) — the fix for a real bug (see `core.py`'s own
  docstring): `screen/server.py` had no way to distinguish "state
  changed" from "no-op," so a press during an already-listening state
  started a second, overlapping mic capture.

## Verified

Live, not just unit-tested: real server kill/restart with CDP console
watching (reconnect), real Groq key acceptance/rejection (smoke check),
real pty testing of `prompt_secret()`'s interactive loop.

## Not covered here

Nothing significant left open from this item specifically — B picked up
directly where A's robustness work left off (barge-in was the next real
gap found).
