# B — Barge-in

## What was reported first, and why it was wrong

Initially reported as "done, mechanism verified" with a caveat filed as
edge-case debt: a realistic short reply measured 1–2ms stop latency, and
a long reply interrupted in its first 500ms occasionally missed budget.

**This framing was firmly and correctly rejected.** Piper's
`synthesize_wav()` blocked the *whole* reply with nothing to abort
mid-call. On a laptop that's ~700ms+ for a two-sentence reply; on a
Raspberry Pi — several times slower, per item C's own measurement — it's
proportionally longer. That means the window where a press couldn't
interrupt her was most of the reply on real target hardware, not a rare
edge case at the start of a long one. "I can't interrupt her" was the
correct description of the bug; "occasional debt" was not.

## What actually fixed it

Sentence-chunked synthesis (built as part of item C, since Google's real
streaming API needed the same interface anyway): `TTSBackend.synthesize_stream()`
yields one WAV per sentence, and `cascade.py`'s `_speak()` checks the
interrupt flag both before requesting the next sentence's audio and
before starting its playback. Bounds the un-interruptible window to
roughly one sentence's synthesis time, for every backend, including ones
(Piper, Kokoro) with no streaming of their own.

## Verified

Real hardware, not simulated: `saathi smoke --barge-in` on the actual
long-reply text, re-run three times after the fix landed —
**stop latency 125ms, 130ms, 125ms**, stable, well under the ~300ms
budget, against a starting point of 775ms on the first uncached run
before any of this session's fixes. No post-interrupt audio bleed
detected in any run.

## Debt left

None specific to barge-in's own mechanism. The remaining latency risk
(TTS first-chunk synthesis time itself, not the interrupt mechanism
around it) is covered in `docs/completed/latency-investigation.md` — a
related but distinct question from "does a press actually stop her."
