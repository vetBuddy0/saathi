# Voice — Google TTS made real; the voice pair

Stream 1 of the 2026-09-25 batch. Territory: `saathi/voice/tts/**`,
its tests, `TODO.md` (one entry), `DECISIONS.md` (appended), this doc.

## The short version

- `saathi/voice/tts/google_backend.py` had never been executed. Run
  live, it was wrong in three ways (below). It is real now: Chirp3-HD
  streams from the Singapore endpoint in English and Mandarin; the
  Neural2/WaveNet backend works too, but through the batch API,
  because **Google's streaming API only accepts Chirp 3: HD voices**
  (400 on anything else; confirmed live and on the first-party page).
- **Voice pair: `en-US-Chirp3-HD-Sulafat` + `cmn-CN-Chirp3-HD-Sulafat`.**
  Same speaker name, same speaker — Chirp3-HD ships its 30 named
  voices in every language it covers, so "recognisably the same
  character" across English and Mandarin is structural, not a matter
  of matching two unrelated voices by ear. No other backend in the
  registry can do this. Sulafat is female and the one Google's own
  voice list calls "Warm". Three runners-up are rendered for ears.
- **Chirp vs Neural2 is decided by structure, not by listening:**
  Neural2 doesn't exist for Mandarin (WaveNet does, and takes 0.3–3 s,
  erratically), can't stream, and has no cross-language speaker. The
  price question the brief raised is moot — Neural2 can't deliver the
  brief's requirements at any price.
- **Listening for warmth is the user's judgement, not mine.** I cannot
  hear these files. The six are in `~/.saathi/tts-compare/`:
  `piper-english.wav`, `piper-chinese.wav`, `google-neural2-english.wav`,
  `google-neural2-chinese.wav`, `google-chirp3-hd-english.wav`,
  `google-chirp3-hd-chinese.wav` (a second run is in `run2/`; the
  four candidate speakers are `chirp3-hd-<Speaker>-<language>.wav`).
  Not committed — audio artifacts don't belong in source control.
- Piper is untouched and remains `DEFAULT_BACKEND_ID` and the automatic
  fallback whenever Google is unavailable; a test now pins that.
- **Two real costs, both outside my territory, both with diffs
  below:** as wired today Chirp adds ~500 ms to time-to-first-audio
  over Piper, and an interrupt that lands while a sentence is still
  rendering waits for Google (1.3–1.5 s measured). Neither is a
  backend problem; both are the one-WAV-per-sentence playback path.

## What was built

`saathi/voice/tts/google_backend.py` — rewritten against the live API.
- `GoogleChirp3HDBackend` (`google-chirp3-hd`): `streaming_synthesize`
  via `texttospeech_v1`, `StreamingAudioConfig(PCM, 24000)`, one
  stream per sentence (DECISIONS 2026-09-18, kept). Chunks are joined
  and wrapped in a WAV header per sentence so the `TTSBackend` contract
  holds. `stream_pcm(language, sentence)` yields the raw chunks as they
  arrive — an additive, `getattr`-discovered capability (the `preload()`
  pattern) for the raw-PCM playback path proposed below. `speaker=`
  constructor argument; default `Sulafat`.
- `GoogleNeural2WaveNetBackend` (`google-neural2`): batch
  `synthesize_speech`, `LINEAR16` at 24 kHz, one call per sentence,
  lazily. Display name says "per-sentence" so the panel is honest.
  Per-language voice table: `en-US-Neural2-C`, `cmn-CN-Wavenet-A`,
  `hi-IN-Neural2-A`, `bn-IN-Wavenet-A` (no Neural2 exists for cmn-CN
  or bn-IN — `list_voices()` at the Singapore endpoint).
- Both: lazy, cached regional client; `available()` gates on the
  library then on `GOOGLE_APPLICATION_CREDENTIALS` pointing at a real
  file, never raises; `voice_for(language)` raises `ValueError` for an
  unsupported language instead of answering in English; `client=`
  injection for tests.

`saathi/voice/tts/compare.py` — the comparison, greenfield.
`uv run python -m saathi.voice.tts.compare` renders the same two
sentences through the three backends with an identical warm-up
(`preload()` where present, then one throwaway synthesis in the same
language), timing TTFA exactly as `cascade._speak()` does: generator
created, clock started, first `next()`, clock stopped. `--candidates`
renders the Chirp speaker shortlist. Unavailable backends are reported
with their reason, never raised on.

Tests: `tests/test_tts_google.py` (voice tables, WAV wrapping, exact
request shapes against a fake client — skip in CI where the library
isn't installed, run here), `tests/test_tts_compare.py` (the timing
protocol including clock-after-generator order, warm-up, concat, skip
path, table), `tests/test_cascade_google_fallback.py` (recipe step 3:
no credentials → both Google backends unavailable with a reason, and a
session preferring either still speaks through Piper). The existing
`tests/test_tts.py` is unchanged and still passes. 322 passed, 1
skipped; ruff and the F821 check clean.

## What the blind draft got wrong (recorded so it isn't re-litigated)

1. **Streaming is Chirp-only.** `400 InvalidArgument: Currently, only
   Chirp 3: HD voices are supported for streaming synthesis.` The
   brief's "streaming, not batch" and "compare Neural2" are mutually
   exclusive on this API. Resolved by keeping Neural2 as batch (the
   same shape as Piper/Kokoro) so the comparison could run; raised
   here rather than narrowed silently.
2. **`en-US-Neural2-C` was sent with every language code.** The API
   rejects a voice/code mismatch; there is no Neural2 Mandarin voice
   to send anyway.
3. **Streaming returns headerless PCM**, not WAV; `paplay` needs a
   header. `pcm_to_wav()` fixes it, and the batch path guards for the
   same in case Google changes it.
4. Minor: `v1beta1` → `v1` (GA surface, same streaming methods at the
   pinned 2.37.0).

## The sentences

Same two, both languages — a greeting with a question and a reminder
offer, things she'd actually hear:

- EN: "Good morning, Auntie, did you sleep well last night?" /
  "It's nearly four o'clock, so shall I remind you about your tablets
  in a little while?"
- ZH: "阿姨，早上好，昨晚睡得好吗？" / "快四点了，等一下要不要我提醒您吃药？"

## The numbers

TTFA is `cascade._speak()`'s number: time from creating the generator
to holding the first sentence's complete WAV. "first chunk" is the
time to Google's first raw PCM chunk — what TTFA would become with the
raw-PCM playback path below. A projection, labelled as such. Laptop,
Singapore endpoint, each backend warmed identically first.

Run 1:

| backend          | language | voice                    | TTFA ms | first chunk ms* | total ms | bytes  | rate  |
|------------------|----------|--------------------------|---------|-----------------|----------|--------|-------|
| piper            | english  | en_US-amy-medium         | 198     | -               | 427      | 385068 | 22050 |
| piper            | chinese  | zh_CN-huayan-medium      | 124     | -               | 272      | 262700 | 22050 |
| google-neural2   | english  | en-US-Neural2-C          | 253     | -               | 560      | 388584 | 24000 |
| google-neural2   | chinese  | cmn-CN-Wavenet-A         | 3077    | -               | 6782     | 344932 | 24000 |
| google-chirp3-hd | english  | en-US-Chirp3-HD-Sulafat  | 718     | 321             | 1649     | 410924 | 24000 |
| google-chirp3-hd | chinese  | cmn-CN-Chirp3-HD-Sulafat | 826     | 349             | 1660     | 409004 | 24000 |

Run 2 (`run2/`):

| backend          | language | voice                    | TTFA ms | first chunk ms* | total ms |
|------------------|----------|--------------------------|---------|-----------------|----------|
| piper            | english  | en_US-amy-medium         | 217     | -               | 546      |
| piper            | chinese  | zh_CN-huayan-medium      | 123     | -               | 283      |
| google-neural2   | english  | en-US-Neural2-C          | 332     | -               | 703      |
| google-neural2   | chinese  | cmn-CN-Wavenet-A         | 273     | -               | 2937     |
| google-chirp3-hd | english  | en-US-Chirp3-HD-Sulafat  | 647     | 280             | 1581     |
| google-chirp3-hd | chinese  | cmn-CN-Chirp3-HD-Sulafat | 813     | 292             | 1621     |

Candidate speakers (Chirp3-HD, both languages, same sentences; TTFA
in ms, EN / ZH): Sulafat 637 / 745, Achernar 397 / 725, Gacrux 623 /
628, Vindemiatrix 553 / 899. Latency differences between speakers are
noise; the point of these files is timbre.

Reading it:
- **Piper is fastest by a wide margin** (120–220 ms) and the only one
  that fits the current 1200 ms cascade budget with room to spare.
  Flat is the complaint, not slow.
- **WaveNet Mandarin is erratic**: 3077 ms then 273 ms to first audio,
  with the second sentence taking ~2.6 s on the fast run. Unusable for
  a Mandarin speaker regardless of how it sounds.
- **Chirp3-HD as wired (sentence-level WAV) costs ~500 ms over
  Piper** on TTFA: 650–830 ms against Piper's 120–220. On top of the
  existing p95 of 1213 ms that puts a real turn at ~1700 ms, over the
  cascade budget. The latency-budget test reads the `turns` table, so
  this will show up the first time the table fills through Chirp.
- **Chirp's first chunk lands at 280–350 ms**, i.e. ~130 ms behind
  Piper, not 500. That is the number a raw-PCM playback path buys
  (proposal 1 below). Only then does Chirp fit the budget.
- Sentence duration: Chirp's two sentences render in ~1.6 s for ~8 s
  of audio — well ahead of real time, so pipelining hides everything
  after the first sentence, same as today.

## Barge-in through Google (recipe step 2)

`saathi run` on port 8765 answered 200. `tts_backend` written as
`google-chirp3-hd` to `~/.saathi/identity.sqlite3`. `smoke.py`'s
`check_barge_in()` builds its session without a `backend_preference`,
so it always resolves `DEFAULT_BACKEND_ID` (Piper) whatever the store
says — I ran the same check with `cascade.DEFAULT_BACKEND_ID` pointed
at Chirp for one process, nothing in the repo changed. Real speaker,
real mic, real `say()` + `interrupt()`:

| backend          | interrupt at | stop latency | post-interrupt bleed | result |
|------------------|--------------|--------------|----------------------|--------|
| piper            | 0.5 s        | 198 ms       | none ('')            | PASS   |
| google-chirp3-hd | 0.5 s        | 1341 ms      | none ('')            | FAIL   |
| google-chirp3-hd | 0.5 s        | 1530 ms      | none ('')            | FAIL   |
| google-chirp3-hd | 2.5 s        | 2 ms         | none ('')            | PASS   |

The audio always stopped — no bleed in any run. What fails is the
`say()` thread's return: at 0.5 s Chirp hasn't produced the first
sentence yet, so `_speak()` is blocked in `pending.get()` and can't see
the interrupt until Google finishes that sentence (1.3–1.5 s for the
long first sentence of `_LONG_REPLY_TEXT`). At 2.5 s the interrupt
lands during playback and stops in 2 ms. This is item B's documented
"one sentence's synthesis" window; Google's per-sentence render is
just longer than Piper's. Proposal 2 below closes it to ~50 ms for
every backend.

## Decisions (all in DECISIONS.md, 2026-09-25)

- Neural2/WaveNet stays as a batch-per-sentence backend rather than
  being deleted.
- Mandarin/Bengali on that backend are WaveNet voices.
- Chirp's voice table is one speaker name for every language;
  default `Sulafat`.
- Streamed PCM is wrapped per sentence; per-chunk WAVs rejected
  (paplay respawn gaps inside a sentence); `stream_pcm()` added for
  the proposed playback path.
- `texttospeech_v1` over `v1beta1`.
- Unsupported language → `ValueError`.
- `compare.py` measures TTFA the cascade way; first-chunk is a
  labelled projection.
- A Google synthesis failure degrades to 100 ms of silence for that
  sentence, logs a WARNING, and takes the backend offline for 60 s so
  the next turn goes to Piper — because cascade's prefetch thread
  swallows exceptions and a raise would hang the session (proposal 6).
  Every Google call has a 10 s deadline. Both from code review.
- **The `tts_backend` preference on this machine was set to
  `google-chirp3-hd` for the barge-in proof and reset to `piper`
  afterwards.** CLAUDE.md makes "what she hears" ask-first; switching
  is one click in the Ctrl+L panel, and that click is the user's. The
  repo's default is unchanged.

## Not verified / couldn't do

- **Warmth.** I can't listen. The pick between Sulafat, Achernar,
  Gacrux and Vindemiatrix — and whether Chirp is warm enough to be
  worth 500 ms — is the user's, from the files above.
- **Pricing re-check**: the pricing page no longer renders its tables
  without JavaScript; the 2026-09-18 figures stand (Neural2 US$16,
  WaveNet US$4, Chirp3-HD US$30 per 1M chars). The US$10/month budget
  alert is the safety net.
- **Pi numbers.** Laptop only; Google's latency is network-bound so
  the Pi should be similar, but Piper's will be several times slower
  there.
- **Hindi and Bengali** voices are in the tables but not rendered
  (the brief asked for English and Mandarin; quota was the reason not
  to render more).
- **arm64 CI** — not run from here.
- Total Google calls this session: ~55 across three minutes, always
  under 50/min; characters in the low thousands, i.e. cents.

## Debt left

- The un-interruptible window during a sentence's render is now
  ~0.7–1.5 s on Chirp (proposal 2).
- TTFA through Chirp is over budget until the raw-PCM playback path
  exists (proposal 1).
- `smoke.py --barge-in` ignores the `tts_backend` preference
  (proposal 3).
- The blind draft's `scripts/setup-pi.sh` and `docs/completed/README.md`
  still say `uv sync --extra google-tts`; it's a dependency *group*:
  `uv sync --group google-tts`. Not my territory; one-line fixes.
- Kokoro's Mandarin dependency gap (item C) is untouched.

## Cross-territory edits needed

### 0. Order of importance

6 (prefetch exceptions) first — it is the one that turns a network
blip into a dead session for *any* backend that can raise. Then 1
(raw-PCM playback, the latency), then 2 (stop latency), then 3–5.

### 1. `audio/playback.py` + `cascade._speak()`: raw-PCM playback for backends that stream

What it buys: TTFA through Chirp drops from ~650–830 ms to ~280–350 ms
(the "first chunk" column), which is what puts Chirp inside the 1200 ms
cascade budget. The backend side already exists
(`GoogleChirp3HDBackend.stream_pcm()`, `sample_rate_hz`).

```diff
--- a/saathi/audio/playback.py
+++ b/saathi/audio/playback.py
@@
+def play_pcm_stream(sink_id: str, chunks, sample_rate_hz: int) -> PlaybackHandle:
+    """Play 16-bit mono PCM chunks as they arrive, through paplay's
+    stdin. Returns as soon as the process is up; a feeder thread
+    writes chunks and closes stdin when the iterator ends. stop()
+    terminates paplay exactly as for a file, so barge-in is unchanged."""
+    proc = subprocess.Popen(
+        [
+            "paplay",
+            f"--device={sink_id}",
+            "--raw",
+            "--format=s16le",
+            "--channels=1",
+            f"--rate={sample_rate_hz}",
+        ],
+        stdin=subprocess.PIPE,
+        stdout=subprocess.DEVNULL,
+        stderr=subprocess.DEVNULL,
+    )
+
+    def _feed() -> None:
+        try:
+            for chunk in chunks:
+                proc.stdin.write(chunk)
+                proc.stdin.flush()
+        except (BrokenPipeError, ValueError):
+            pass  # stopped mid-sentence; the process is gone
+        finally:
+            try:
+                proc.stdin.close()
+            except Exception:
+                pass
+
+    threading.Thread(target=_feed, daemon=True).start()
+    return PlaybackHandle(proc)
```

```diff
--- a/saathi/voice/engine/cascade.py
+++ b/saathi/voice/engine/cascade.py
@@ def _speak(self, text: str) -> None:
         backend = self._current_backend()
+        stream_pcm = getattr(backend, "stream_pcm", None)
+        if stream_pcm is not None:
+            self._speak_streaming(backend, stream_pcm, sentences)
+            return
         stream = backend.synthesize_stream(self._last_language, sentences)
@@
+    def _speak_streaming(self, backend, stream_pcm, sentences: list[str]) -> None:
+        """Same interrupt checks as _speak(), but each sentence plays
+        from its first chunk instead of its last. TTFA is the first
+        chunk. Prefetch of sentence N+1 opens its stream while N plays;
+        Google buffers server-side until we read."""
+        speak_started_at = time.monotonic()
+        first_chunk = True
+        for sentence in sentences:
+            with self._playback_lock:
+                if self._interrupt_requested:
+                    return
+            chunks = stream_pcm(self._last_language, sentence)
+            try:
+                head = next(chunks)
+            except StopIteration:
+                continue
+            with self._playback_lock:
+                if self._interrupt_requested:
+                    return
+                if first_chunk:
+                    self._finalize_turn_timings(
+                        first_tts_chunk_ms=round((time.monotonic() - speak_started_at) * 1000)
+                    )
+                    first_chunk = False
+                handle = play_pcm_stream(
+                    self._sink_id, itertools.chain([head], chunks), backend.sample_rate_hz
+                )
+                self._current_playback = handle
+            try:
+                handle.wait()
+            finally:
+                with self._playback_lock:
+                    self._current_playback = None
```

Needs `tests/test_cascade.py` coverage with a fake `stream_pcm` backend
and `play_pcm_stream` monkeypatched. Leaves the WAV path untouched for
Piper/Kokoro/Neural2.

### 2. `cascade._speak()`: don't block on `pending.get()` past an interrupt

What it buys: stop latency bounded by ~50 ms for every backend even when
the interrupt lands while a sentence is still rendering — the 1341/1530
ms failures above become passes. Helps Piper on a Pi too.

```diff
--- a/saathi/voice/engine/cascade.py
+++ b/saathi/voice/engine/cascade.py
@@ def _speak(self, text: str) -> None:
-            with self._playback_lock:
-                if self._interrupt_requested:
-                    return  # interrupted since the last sentence: don't wait on the next
-            wav_bytes = pending.get()
+            while True:
+                with self._playback_lock:
+                    if self._interrupt_requested:
+                        return  # interrupted: don't wait on a sentence nobody will hear
+                try:
+                    wav_bytes = pending.get(timeout=0.05)
+                    break
+                except queue.Empty:
+                    continue
```

The abandoned prefetch thread is a daemon and simply finishes on its
own, exactly as today after an interrupt mid-playback.

### 3. `smoke.py check_barge_in()`: honour the stored `tts_backend`

So `saathi smoke --barge-in` proves the interrupt path through whatever
voice she actually has, not always Piper.

```diff
--- a/saathi/smoke.py
+++ b/saathi/smoke.py
@@ def check_barge_in(
-    session = CascadeSession(handles.sink_id, client=object())
+    from saathi.identity.preferences import TTS_BACKEND_KEY, threadsafe_reader
+    from saathi.identity.store import IdentityStore
+    from saathi.voice.tts.registry import DEFAULT_BACKEND_ID
+
+    store_path = Path.home() / ".saathi" / "identity.sqlite3"
+    backend_preference = (
+        threadsafe_reader(IdentityStore(store_path), TTS_BACKEND_KEY, DEFAULT_BACKEND_ID)
+        if store_path.exists()
+        else (lambda: DEFAULT_BACKEND_ID)
+    )
+    session = CascadeSession(
+        handles.sink_id, client=object(), backend_preference=backend_preference
+    )
```

(The store path should come from wherever `cli.py` resolves it, not be
repeated; shown literally only to make the intent exact.)

### 4. `SPEC.md` — proposed, not applied

Under "Budgets", after the table:

```diff
+**Time-to-first-audio is measured from the first audio chunk, not the
+first complete sentence.** A streaming voice (Google Chirp3-HD) hands
+back its first 240 ms of audio ~300 ms after the request and the rest
+while that plays; waiting for the whole sentence throws that away and
+costs ~500 ms per turn. Batch voices (Piper) are unaffected: their
+first chunk is their first sentence.
```

Under "Scope", "In:" — no change needed; "Never" — no change.

### 6. `cascade._prefetch_next_chunk()`: forward exceptions, fall back to Piper

What it buys: a synthesis exception reaches `_speak()` instead of
leaving it blocked on `pending.get()` forever. Today the Google
backends work around this by never raising (silence + cooldown); with
this in place they could raise and `_speak()` could re-render the
remaining sentences through Piper in the same turn.

```diff
--- a/saathi/voice/engine/cascade.py
+++ b/saathi/voice/engine/cascade.py
@@ def _prefetch_next_chunk(stream) -> "queue.Queue":
     def _run() -> None:
-        result.put(next(stream, _STREAM_DONE))
+        try:
+            result.put(next(stream, _STREAM_DONE))
+        except Exception as exc:  # the backend died mid-sentence
+            result.put(exc)
@@ def _speak(self, text: str) -> None:
             wav_bytes = pending.get()
             if wav_bytes is _STREAM_DONE:
                 return
+            if isinstance(wav_bytes, Exception):
+                logger.warning("%s failed mid-reply: %s; finishing via %s",
+                               backend.id, wav_bytes, DEFAULT_BACKEND_ID)
+                if backend.id == DEFAULT_BACKEND_ID:
+                    return  # Piper itself failed; nothing further to fall back to
+                backend = self._backends[DEFAULT_BACKEND_ID]
+                stream = backend.synthesize_stream(self._last_language, sentences[spoken:])
+                pending = _prefetch_next_chunk(stream)
+                continue
```

(`spoken` is a counter of sentences already played, to add alongside
`first_chunk`. With this landed, `_GoogleBackend._guarded()` can be
reduced to the cooldown bookkeeping and re-raise.)

### 5. `docs/completed/README.md` and `scripts/setup-pi.sh`

`uv sync --extra google-tts` → `uv sync --group google-tts` (it's a
dependency group in `pyproject.toml`, not an extra; the `--extra` form
fails). The README's "Google TTS — exactly what's needed" section is
now done and can point here instead. Index line to add:

```diff
+- [Voice](voice.md) — Google TTS made real (Chirp3-HD streams,
+  Neural2 can't); the Sulafat voice pair; six-file comparison numbers
```
