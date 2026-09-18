# Latency investigation — 2026-09-18

Triggered by a real measured sample from item E's instrumentation work:
one real turn ran 2540ms against the cascade "Voice starts" budget of
1200ms, dominated in that single sample by a 1677ms Whisper call. This
investigation is the follow-up, done properly with repeated real calls
rather than one sample.

**Headline: that single 2540ms/1677ms-STT sample does not hold up as a
representative case.** Ten fresh real trials put median STT at ~255ms,
not ~1700ms — the first sample was very likely a cold-start anomaly
(first call of the process, first TLS handshake, or a Groq-side cold
path), not the steady-state number. The real, repeatable bottleneck
turned out to be somewhere else entirely: **TTS first-chunk synthesis**,
not STT and not the network.

## 1. Network round-trip to Groq

Measured directly with `curl` against `api.groq.com`, and independently
cross-checked from a real Singapore vantage point via check-host.net
(a third-party multi-location ping service) to rule out this being an
artifact of wherever this development machine happens to sit.

- This machine's own egress IP geolocates to Singapore (NUS network,
  confirmed via `ipinfo.io`) — a plausible proxy for where a Singapore
  Pi would sit.
- `api.groq.com` resolves behind Cloudflare's anycast network. A ping
  to it from check-host.net's actual Singapore node returned **~2ms**
  — Cloudflare has an edge point of presence very close to (or in)
  Singapore, so a real device there is not making a raw cross-Pacific
  TCP/TLS round trip on every call.
- Real `curl` timing against `api.groq.com`, 5 warm requests on a reused
  connection: **TCP connect ~4ms, TLS handshake ~28ms (once), TTFB
  ~213–225ms per request even for a trivial `GET /models` call.**

**Conclusion: it is not "crossing the Pacific twice."** The ~200–225ms
seen on every call, warm or cold, is Groq's own API gateway/auth/routing
overhead (present on the smallest possible request), not international
transit — Cloudflare's anycast network already resolves that. This
number is a floor under every call this device makes, but it is not the
2540ms problem.

## 2. What we upload: WAV vs FLAC vs Opus

Confirmed in code before touching anything: `cascade.py` was sending
raw, uncompressed 16kHz mono 16-bit PCM wrapped in a WAV header
(`_pcm_to_wav_bytes`, now removed) on every turn.

Real audio (a real ~3.9s Piper-synthesized utterance, resampled to the
exact 16kHz mono format `audio/capture.py`'s `parec` actually produces —
not a made-up format), sent to the real Whisper endpoint, 5 trials each:

| Format | Size | Median STT time |
|---|---|---|
| WAV (as shipped) | 123,572 B | 282ms |
| FLAC | 67,249 B (−46%) | 251ms (−11%) |
| Opus/ogg | 46,120 B (−63%) | 234ms (−17%) |

**This was the real, actionable win.** Switched the STT upload to FLAC
(`_pcm_to_flac_bytes`, via `soundfile` — a small, real aarch64 wheel,
1.2MB, confirmed installable on the Pi). Opus was faster still, but only
by another ~17ms over FLAC, and needs PyAV/ffmpeg — a genuinely heavy
dependency (in the same weight class as Kokoro's `torch` problem this
project already ruled out once) for a small further gain. Not worth it.
FLAC is now the default; `qwen/qwen3.8-27b`'s cheaper, more consistent
completion also helped (see below).

## 3. End-of-utterance detection

Checked directly, not assumed: `saathi/audio/vad.py` (Silero VAD)
exists in this codebase but **is never imported or called** from
`screen/server.py` or `cascade.py`. This device is push-to-talk —
end-of-utterance is the literal spacebar release event, handled
synchronously in the WebSocket handler the instant it arrives. **There
is no silence-threshold delay anywhere in this path.** `vad.py` is real,
tested code, just not wired into the cascade turn today; it exists for
SPEC's other named uses (barge-in detection in a non-push-to-talk mode,
if one is ever built), not this one.

## 4. Before/after, from real repeated turns

Ten real trials each, same real audio, same real Piper TTS (pre-warmed,
matching how `end_turn()`'s background preload behaves in production),
logged into two real `IdentityStore` `turns` tables and read back
through the actual `latency_budget.py` percentile logic — not estimated:

| | before (WAV, `openai/gpt-oss-120b`) | after (FLAC, `qwen/qwen3.8-27b`) |
|---|---|---|
| STT (median) | ~264ms | ~247ms |
| LLM first-token¹ (median) | ~577ms | ~178ms |
| TTS first-chunk (median) | ~579ms | ~578ms |
| **Voice-starts p95** | **1656ms** | **1237ms** |
| Voice-starts budget | 1200ms | 1200ms |
| **Result** | **FAIL** | **FAIL, by 37ms** |
| Brain-finishes p95 | 986ms | 767ms |
| Brain-finishes budget (3000ms) | pass | pass |

¹ "LLM first-token" is the whole (non-streamed) completion call, same
honest caveat as `TurnTimings.first_token_ms` everywhere else in this
codebase — see `cascade.py`.

The switch to `qwen/qwen3.8-27b` (decided for item D's own reasons —
correctness, not speed) turned out to also help latency a lot: its
completions are shorter and far more consistent than `gpt-oss-120b`'s,
which was spending 400–700ms per call partly on invisible reasoning
tokens (see item D/E's earlier finding about hidden reasoning-token
cost). That, plus the FLAC upload, took the real p95 from 1656ms to
1237ms — a 25% cut.

**TTS first-chunk synthesis is now the largest and most variable single
stage** (426–808ms across trials, correlated with reply length), not
STT and not the network. Piper is a CPU-bound, non-streaming synthesizer
run on a laptop CPU; the Raspberry Pi this targets is, per item C's own
established finding, "several times slower" than this laptop for the
exact same work. Whatever margin exists here on a laptop should not be
assumed to exist on the Pi.

## Verdict — 1200ms is not reliably reachable today

**Not tuning the test to pass, per instruction.** The real, honest
result: 1237ms p95 against a 1200ms budget, on a laptop, after real
fixes. That is close, not passing, and the dominant remaining cost (TTS
synthesis) is expected to be *worse*, not better, on the actual target
hardware.

**What it would take to actually close the gap:**
1. **Re-run this exact before/after on a real Pi**, not just this
   laptop — the honest next step, not more laptop tuning. Nothing here
   was verified against target hardware.
2. **A faster or streaming local TTS backend for the first sentence.**
   Item C's own comparison already flagged Google's real streaming
   synthesis API as architecturally suited to exactly this problem
   (audio starts before the whole sentence is even done rendering) —
   still untested end-to-end because there are no GCP credentials
   configured yet.
3. **A smaller/faster local Piper voice** (a `low`-quality model
   instead of `medium`) as a lower-effort alternative, trading voice
   quality for latency — not attempted here; a real, measurable
   tradeoff someone should hear before it's decided.
4. Opus over FLAC would claw back another ~17ms on the STT leg alone,
   at the cost of the PyAV/ffmpeg dependency — marginal next to the TTS
   number above; not recommended unless the Pi numbers change this math.

Shipped at the time: FLAC upload (`cascade.py`, `_pcm_to_flac_bytes`),
`qwen/qwen3.8-27b` as the default model (item D). Not shipped: any
TTS-latency fix — that's a real, undecided tradeoff (voice quality vs.
speed, or a Google credential-setup dependency), not something to pick
without you hearing the options.

## 5. Follow-up (2026-09-19) — TTS pipelining + startup warm

Two real, shipped fixes, re-measured properly, not estimated:

- **Pipelined synthesis.** `_speak()` used to synthesize sentence N+1
  only *after* sentence N finished playing — fully sequential.
  `_prefetch_next_chunk()` now starts N+1's synthesis the moment N is
  pulled off the pipeline, so it runs concurrently with N's playback.
  Real, controlled, same-hardware comparison (real Piper, real sink,
  same 3-sentence text, back to back): **old sequential 4732ms → new
  pipelined 4378ms — 354ms / 7% faster** for the whole reply.
- **Startup warm.** `CascadeSession.__init__` now calls
  `_preload_voice_in_background()` immediately at construction, not
  only during `end_turn()`'s STT/LLM wait — the fix for the 775ms
  cold-start number that triggered item 3 in the first place, which
  only benefited turn 2 onward before this. Confirmed working: across
  10 fresh real trials on a freshly constructed session, trial 1's
  `tts_first_chunk_ms` (550ms) sat inside the normal 401–670ms range
  the other 9 trials showed — no cold-start spike on the very first
  turn anymore.

**Real p95, both fixes in place, one long-lived session across 10 real
turns (matching how `cli.py` actually uses `CascadeSession` — not a
fresh object per turn):**

| | after item 3's fixes | after pipelining + warm start |
|---|---|---|
| Voice-starts p95 | 1237ms | **1213ms** |
| Voice-starts budget | 1200ms | 1200ms |
| Result | FAIL, by 37ms | **FAIL, by 13ms** |
| Brain-finishes p95 | 767ms | 652ms |

**Still not passing — honestly reported, budget untouched, as
instructed.** Closer, not there. The reason this moved so little:
**pipelining and startup-warm both target costs *after* or *around* the
first chunk — neither touches the first sentence's own synthesis time**,
which is exactly what `voice_starts`/the 1200ms budget measures (time
to the *first* audio). Pipelining's real win (the 7% figure above) shows
up in *total reply duration* for multi-sentence replies, not in
"Voice starts." Startup-warm's real win is eliminating a one-time,
first-turn-of-a-process spike, not the steady-state number — and this
device's turns were already steady-state warm in every measurement
that fed the original 1237ms figure, so there was little cold-start
left for it to remove.

**What would actually move the "Voice starts" number** is unchanged
from item 3's original list: a faster/streaming first-sentence
synthesis path is what the remaining ~400–600ms of first-sentence Piper
synthesis needs, not pipelining. Re-run on real Pi hardware is still the
most important unverified step; nothing in this follow-up touched that
gap either.

## 6. Decision (2026-09-19) — stop optimizing Piper here

1213ms against a 1200ms budget is noise at this point, not a gap worth
closing with more Piper-specific tuning (a smaller/faster local voice,
etc.) — that work would be thrown away the moment Google's streaming
TTS is unblocked (GCP credentials, still not configured — see
`docs/completed/README.md`), which is architecturally the real fix for
first-chunk latency (audio starts before the whole sentence even
finishes rendering, not just synthesized faster on the same blocking
path). Pipelining and the startup warm-up were kept: they help *any*
backend, Piper or Google, so they weren't wasted effort regardless of
which TTS backend eventually wins. **The budget test stays red, reported
honestly, until Google's streaming path is actually tried** — no further
Piper-latency work planned unless that path turns out not to help.
