"""Hands-free start: she says "Saathi" and it listens, no button.

Why it exists: the spacebar stands in for a wearable button (SPEC.md,
"Triggers"), and a name spoken from across the room is the fallback for
when the button isn't on her. The local part is the point, not an
optimisation: nothing leaves the device until the name is heard here.
Audio goes to the cloud transcriber only for the turn that follows.

How: Silero VAD (already here for the speech gate) cuts the always-open
echo-cancelled mic into utterances; each short utterance is transcribed
on-device by faster-whisper `tiny.en` and matched against the name.
Silence costs one VAD call per 32 ms; whisper runs only on speech, and
only while the device is idle.

Contested -- openWakeWord lost, though SPEC.md names it. It has no
"Saathi": a new word needs a custom model trained on thousands of
synthetic clips, and its pretrained models are CC BY-NC-SA
(non-commercial). Local keyword spotting with a small whisper hears any
name with no training. Measured on the 800-series laptop (2026-10-07,
Piper-synthesised clips): `tiny.en` heard "Saathi" in the English and
Hindi voices in ~180 ms per clip and never turned "Sorry about that",
"Satisfied" or "That's it" into the name; `base` (multilingual) was
3x slower and dropped the name from "Saathi, play a song"; `small.en`
was 5x slower and no better. Override with SAATHI_WAKE_MODEL.

Contested -- matching is fuzzy, on purpose. Whisper spells an Indian
name several ways ("Saathi", "Sathi", "Sothai"). A similarity of 0.82
to "saathi" over the first three words takes those and rejects "sahi"
and "sath" (common Hindi words, 0.80) and "sorry" (0.18). A false wake
costs one cloud turn that the speech gate usually ends silently; a missed
wake costs her repeating herself, so the line leans toward hearing it.
"Sati" scores 0.80 and is listed by hand: on Indian-English voices it was
tiny.en's most common spelling of the name (21 of 56 clips missed for that
alone, 2026-10-07), and unlike "sahi" it is not an everyday word.

Hearing the name early (2026-10-07). Waiting for the utterance to end
before checking it meant "Saathi call Udhi" could only wake the device
after the whole sentence and its pause. Now the utterance is also
checked while she is still speaking, at a few points after speech
starts (`_EARLY_CHECK_SECONDS`); the first check that hears the name
wakes the device at once, and everything after that point streams into
the turn through `follow()` -- the listener's own mic, handed over, so
there is no gap and no second `parec` starting up mid-sentence. The
final whole-utterance check still runs when no early one fired. Early
checks are skipped while the transcriber is busy, so a talkative room
costs at most one whisper call at a time, as before. Lost: a fixed
"first 1.2 s" window -- a quick "Saathi" is 0.6 s and a slow one 0.9 s,
so one window either clips the name or waits for nothing.

Ending a one-breath command early. A turn that started this way ends on
the endpointer's 0.8 s pause like any hands-free turn, but while it
streams the listener keeps cutting it at the 0.3 s pause and asks
`is_complete` (cli.py: the command router) whether the words so far are
already a whole command ("call Udhi"). If so the turn ends there rather
than 0.5 s later. Only *when* to stop is decided locally; what she said
is still transcribed by the cloud STT. Lost: always ending at 0.3 s
(the old one-breath path) -- "Saathi, call... Udhi" with a breath in it
lost the name.
"""

from __future__ import annotations

import difflib
import logging
import os
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from saathi.audio.capture import Capture
from saathi.audio.vad import CHUNK_BYTES, SAMPLE_RATE, VoiceActivityDetector

logger = logging.getLogger(__name__)

WAKE_WORD = "saathi"
DEFAULT_WAKE_MODEL = "tiny.en"
_SIMILARITY = 0.82
# Spellings seen from whisper that score under the line but are the name.
_KNOWN_MISHEARINGS = frozenset({"sothai", "sati"})
_WAKE_WITHIN_WORDS = 3

_CHUNK_SECONDS = CHUNK_BYTES / 2 / SAMPLE_RATE  # 32 ms
# Seconds of speech (from its onset, not counting the preroll) at which
# an utterance still in progress is checked for the name. Measured on
# Piper and Google en-IN clips, quick and normal speed (2026-10-07): see
# DECISIONS.md. The first is about one quick "Saathi"; later ones catch
# a slower name or one tiny.en only spells right with more context.
_EARLY_CHECK_SECONDS = (0.55, 0.9, 1.3)

Transcriber = Callable[[bytes], str]


def _is_name(word: str) -> bool:
    if word in _KNOWN_MISHEARINGS:
        return True
    return difflib.SequenceMatcher(None, word, WAKE_WORD).ratio() >= _SIMILARITY


def match_wake_word(text: str) -> tuple[bool, str]:
    """Whether `text` calls her by name, and what was said after it
    ("Saathi, play a song" -> (True, "play a song")). The name must be
    among the first few words: "I told my saathi" is talk, not a call."""
    words = re.findall(r"[a-z]+", text.lower())
    for i in range(min(len(words), _WAKE_WITHIN_WORDS)):
        if _is_name(words[i]):
            return True, " ".join(words[i + 1 :])
        if i + 1 < len(words) and _is_name(words[i] + words[i + 1]):
            return True, " ".join(words[i + 2 :])
    return False, ""


# How the *cloud* transcriber has spelled the name when it was all she
# said (live, 2026-10-08: "Saathi" alone came back "Saudi?" and was
# answered). Only trusted on a turn that began with her name (`loose`):
# "Sorry, what time is it?" said over the spacebar keeps its "Sorry", and
# none of these ever wakes the device (match_wake_word doesn't use them).
_CLOUD_MISHEARINGS = frozenset({"saudi", "sorry", "sadhi", "saadi", "saati", "sathee"})
# Said before the name, never meant as content: "Hey Saathi".
_GREETINGS = frozenset({"hey", "hi", "hello", "ok", "okay", "oh"})


def _leading_name_words(lowered: list[str], loose: bool) -> int:
    """How many of the leading words are her name (0 if it doesn't
    start with it): one word, two run together ("saa thi"), either
    after a greeting ("hey saathi")."""

    def is_name(word: str) -> bool:
        return _is_name(word) or (loose and word in _CLOUD_MISHEARINGS)

    for skip in (0, 1):
        if skip and (not lowered or lowered[0] not in _GREETINGS):
            break
        rest = lowered[skip:]
        if rest and is_name(rest[0]):
            return skip + 1
        if len(rest) > 1 and is_name(rest[0] + rest[1]):
            return skip + 2
    return 0


def strip_wake_word(text: str, *, loose: bool = False) -> str:
    """`text` without her name at its very start: the cloud transcriber
    spells it as freely as tiny.en does ("Sothai, call Udhi"), and a
    command after it should still read as a command. Only a leading
    name is taken -- "call Sathi" keeps its object. `loose`: the turn
    began with her name, so the cloud's other spellings of it count too
    (`_CLOUD_MISHEARINGS`). A name said twice ("Saathi... Saathi, play")
    is taken twice."""
    while True:
        words = re.findall(r"[^\W_]+(?:'[^\W_]+)?", text, flags=re.UNICODE)
        drop = _leading_name_words([w.lower() for w in words], loose)
        if not drop:
            return text
        # Cut the original string after the dropped words, keeping her
        # punctuation and script for whatever reads it next.
        position = 0
        for word in words[:drop]:
            position = text.index(word, position) + len(word)
        text = text[position:].lstrip(" ,.!?;:-")


def is_only_name(text: str, *, loose: bool = False) -> bool:
    """Whether `text` says nothing but her name ("Saathi?", "Hey
    Saathi", and -- `loose` -- "Saudi?"). Then she is calling, not
    asking: the turn has no content for the model."""
    if not re.search(r"[^\W_]", text, flags=re.UNICODE):
        return False  # nothing at all is silence, not her name
    return not re.search(r"[^\W_]", strip_wake_word(text, loose=loose), flags=re.UNICODE)


class UtteranceSegmenter:
    """VAD chunks in, whole utterances out: speech start (with a little
    audio before it, so the first syllable isn't clipped) to the first
    `silence_seconds` of quiet. Utterances longer than `max_seconds` are
    dropped whole -- a radio, not someone calling her."""

    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        *,
        preroll_seconds: float = 0.3,
        silence_seconds: float = 0.3,
        max_seconds: float = 8.0,
    ) -> None:
        self._is_speech = is_speech
        self._preroll: deque[bytes] = deque(maxlen=max(1, round(preroll_seconds / _CHUNK_SECONDS)))
        self._silence_chunks = max(1, round(silence_seconds / _CHUNK_SECONDS))
        self.silence_seconds = self._silence_chunks * _CHUNK_SECONDS
        self._max_chunks = round(max_seconds / _CHUNK_SECONDS)
        self._speech: list[bytes] | None = None
        self._quiet = 0
        self._too_long = False
        self._since_onset = 0

    def reset(self) -> None:
        self._preroll.clear()
        self._speech = None
        self._quiet = 0
        self._too_long = False
        self._since_onset = 0

    @property
    def in_utterance(self) -> bool:
        return self._speech is not None and not self._too_long

    @property
    def chunks_since_onset(self) -> int:
        """Chunks pushed since speech started (0 between utterances)."""
        return self._since_onset if self._speech is not None else 0

    def current(self) -> bytes | None:
        """The utterance so far, preroll included, or None."""
        return b"".join(self._speech) if self.in_utterance else None

    def push(self, chunk: bytes) -> bytes | None:
        speech = self._is_speech(chunk)
        if self._speech is None:
            if speech:
                self._speech = [*self._preroll, chunk]
                self._quiet = 0
                self._too_long = False
                self._since_onset = 1
            else:
                self._preroll.append(chunk)
            return None
        self._since_onset += 1
        self._quiet = 0 if speech else self._quiet + 1
        if not self._too_long:
            self._speech.append(chunk)
            if len(self._speech) > self._max_chunks:
                self._too_long = True
                self._speech = []
        if self._quiet < self._silence_chunks:
            return None
        done = None if self._too_long else b"".join(self._speech)
        self._speech = None
        self._preroll.clear()
        return done


class Endpointer:
    """When a hands-free turn is over, with no key to let go of: after
    she has spoken and then paused for `silence_seconds`, or if she says
    nothing for `no_speech_seconds`, or at `max_seconds` regardless.
    `last_speech_at` (on `clock`) is when it last heard her, so the
    server can log how long the turn waited after her last word
    (`turns.eou_ms`) rather than how long the turn was."""

    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        *,
        silence_seconds: float = 0.8,
        no_speech_seconds: float = 5.0,
        max_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._is_speech = is_speech
        self._silence_chunks = round(silence_seconds / _CHUNK_SECONDS)
        self._no_speech_chunks = round(no_speech_seconds / _CHUNK_SECONDS)
        self._max_chunks = round(max_seconds / _CHUNK_SECONDS)
        self._heard = False
        self._quiet = 0
        self._total = 0
        self._buffer = b""
        self._clock = clock
        self.last_speech_at: float | None = None

    def push(self, audio: bytes) -> bool:
        """Any number of bytes in; True once the turn is over."""
        self._buffer += audio
        while len(self._buffer) >= CHUNK_BYTES:
            chunk, self._buffer = self._buffer[:CHUNK_BYTES], self._buffer[CHUNK_BYTES:]
            self._total += 1
            if self._is_speech(chunk):
                self._heard = True
                self._quiet = 0
                self.last_speech_at = self._clock()
            else:
                self._quiet += 1
            if self._heard and self._quiet >= self._silence_chunks:
                return True
            if not self._heard and self._total >= self._no_speech_chunks:
                return True
            if self._total >= self._max_chunks:
                return True
        return False


def vad_is_speech() -> Callable[[bytes], bool]:
    """A fresh Silero detector per caller: its recurrent state must not
    be shared between the wake listener and a turn's endpointer."""
    return VoiceActivityDetector().is_speech


def local_transcriber(
    model_name: str | None = None, prompt: Callable[[], str | None] | None = None
) -> Transcriber:
    """On-device STT for the name only. The model loads on first use
    (a few seconds) -- `WakeWordListener.start()` warms it in the
    background so the first "Saathi" isn't the slow one.

    `prompt()`, read per clip, replaces the plain "Saathi" bias with a
    longer one when there is something to expect: while music plays,
    cli.py hands the media commands ("Saathi, stop. Saathi, pause."), so
    the short command after the name is spelled as a command and the
    turn can end at its first pause (`is_complete`)."""
    import numpy as np
    from faster_whisper import WhisperModel

    name = model_name or os.environ.get("SAATHI_WAKE_MODEL", DEFAULT_WAKE_MODEL)
    # It logs every clip it hears; while idle that is the whole room.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    holder: dict[str, WhisperModel] = {}
    lock = threading.Lock()

    def transcribe(pcm: bytes) -> str:
        with lock:
            if "model" not in holder:
                holder["model"] = WhisperModel(name, device="cpu", compute_type="int8")
            model = holder["model"]
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = model.transcribe(
            audio,
            beam_size=1,
            language="en" if name.endswith(".en") else None,
            # Biases the spelling of her name (and, while music plays,
            # of the commands she gives it).
            initial_prompt=(prompt() if prompt is not None else None) or "Saathi",
            condition_on_previous_text=False,
            vad_filter=False,
        )
        return " ".join(s.text for s in segments).strip()

    return transcribe


@dataclass(frozen=True)
class _Check:
    """One piece of audio for the worker to transcribe. `kind`: "early"
    (an utterance still in progress), "final" (a whole utterance), or
    "complete" (a followed turn so far: is it a whole command yet?).
    `end` is the index of the last mic chunk in `pcm`, so whatever
    arrived after it can be handed to the turn without a gap or a
    repeat."""

    kind: str
    pcm: bytes
    end: int
    utterance: int
    # When she last spoke in `pcm` (time.monotonic()), for "final": the
    # pause that closed the utterance, counted back from when it closed.
    last_speech_at: float | None = None


class _Following:
    """The listener's mic, handed to a turn: what `follow()` returns.
    `stop()` takes it back."""

    def __init__(self, listener: "WakeWordListener", on_chunk, on_complete) -> None:
        self._listener = listener
        self.on_chunk = on_chunk
        self.on_complete = on_complete
        self.audio: list[bytes] = []

    def stop(self) -> None:
        self._listener._unfollow(self)


class WakeWordListener:
    """Keeps the echo-cancelled mic open and calls
    `on_wake(pcm, rest, after, follow=follow)` (from a worker thread)
    when she says the name. `pcm` is the audio the name was heard in;
    `rest` is what she said after it when that audio is a whole
    utterance (non-empty: the request is already in `pcm`), and "" when
    the name was heard early, while she was still talking.

    `follow(on_chunk, on_complete=None)` hands this listener's mic to
    the turn: every chunk heard after `pcm` -- including those that
    arrived while the name was being checked -- goes to `on_chunk`, in
    order, until the returned handle's `stop()`. `on_complete()` is
    called (worker thread) if the turn so far already sounds like a
    whole command (`is_complete`), so the turn can end before the
    endpointer's pause. `after()` is the older, simpler form: the audio
    heard since `pcm`, for a caller that opens its own capture.

    Only listens while `should_listen()` is True (the device is idle),
    or while followed."""

    def __init__(
        self,
        source_id: str,
        on_wake: Callable[..., None],
        should_listen: Callable[[], bool],
        transcriber: Transcriber | None = None,
        is_speech: Callable[[bytes], bool] | None = None,
        capture_factory=Capture,
        is_complete: Callable[[str], bool] | None = None,
        early_check_seconds: tuple[float, ...] = _EARLY_CHECK_SECONDS,
    ) -> None:
        self._source_id = source_id
        self._on_wake = on_wake
        self._should_listen = should_listen
        self._transcriber = transcriber or local_transcriber()
        self._segmenter = UtteranceSegmenter(is_speech or vad_is_speech())
        self._capture_factory = capture_factory
        self._is_complete = is_complete
        self._early_chunks = {max(1, round(s / _CHUNK_SECONDS)) for s in early_check_seconds}
        self._capture = None
        self._queue: queue.Queue[_Check | None] = queue.Queue(maxsize=4)
        self._worker: threading.Thread | None = None
        # Guards everything the mic thread and the worker/loop share.
        self._lock = threading.RLock()
        self._index = 0  # mic chunks seen while listening
        # The last few seconds of mic, numbered, kept even while the
        # device is busy: a turn handed the mic must get every chunk
        # after the name, including those that arrived while the server
        # was still switching state.
        self._recent: deque[tuple[int, bytes]] = deque(maxlen=round(3.0 / _CHUNK_SECONDS))
        self._last_end = 0  # index of the last chunk of the last whole utterance
        self._utterance = 0  # which utterance the segmenter is in
        self._woken_by = -1  # the utterance that already woke the device
        self._busy = False
        self._following: _Following | None = None

    def start(self) -> None:
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()
        # Warm the model now: an empty clip loads it without a real call.
        threading.Thread(target=self._warm, daemon=True).start()
        self._capture = self._capture_factory(self._source_id, self._on_chunk, CHUNK_BYTES)
        self._capture.start()

    def stop(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        self._queue.put(None)

    def _warm(self) -> None:
        try:
            self._transcriber(b"\x00\x00" * (SAMPLE_RATE // 2))
        except Exception:
            logger.exception("wake word model failed to load")

    # -- the mic thread ---------------------------------------------------

    def _on_chunk(self, chunk: bytes) -> None:
        with self._lock:
            following = self._following
            if following is not None:
                following.on_chunk(chunk)
                following.audio.append(chunk)
                if self._segmenter.push(chunk) is not None and self._is_complete is not None:
                    # She paused: is the turn so far a whole command?
                    self._enqueue(_Check("complete", b"".join(following.audio), -1, -1))
                return
            self._index += 1
            self._recent.append((self._index, chunk))
            if not self._should_listen():
                self._segmenter.reset()
                self._last_end = self._index  # nothing before now is "after" anything
                return
            starting = not self._segmenter.in_utterance
            utterance = self._segmenter.push(chunk)
            if starting and self._segmenter.in_utterance:
                self._utterance += 1
            if utterance is not None:
                self._last_end = self._index
                if self._woken_by != self._utterance:
                    self._enqueue(
                        _Check(
                            "final",
                            utterance,
                            self._index,
                            self._utterance,
                            time.monotonic() - self._segmenter.silence_seconds,
                        )
                    )
                return
            if (
                self._segmenter.chunks_since_onset in self._early_chunks
                and self._woken_by != self._utterance
                and not self._busy
                and self._queue.empty()
            ):
                pcm = self._segmenter.current()
                if pcm is not None:
                    self._enqueue(_Check("early", pcm, self._index, self._utterance))

    def _enqueue(self, check: _Check) -> None:
        try:
            self._queue.put_nowait(check)
        except queue.Full:
            pass  # still busy with earlier speech; this one is dropped


    # -- the worker -------------------------------------------------------

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            with self._lock:
                self._busy = True
            try:
                self._run_check(item)
            finally:
                with self._lock:
                    self._busy = False

    def check(self, utterance: bytes) -> None:
        """A whole utterance, checked now (tests, and anything driving
        the listener without a mic)."""
        self._run_check(_Check("final", utterance, self._index, -2))

    def _run_check(self, check: _Check) -> None:
        if check.kind == "complete":
            self._check_complete(check)
            return
        with self._lock:
            if not self._should_listen() or self._woken_by == check.utterance >= 0:
                return
        try:
            text = self._transcriber(check.pcm)
        except Exception:
            logger.exception("wake word transcription failed")
            return
        heard, rest = match_wake_word(text)
        if not heard:
            return
        with self._lock:
            if not self._should_listen() or self._woken_by == check.utterance >= 0:
                return
            self._woken_by = check.utterance
        early = check.kind == "early"
        logger.info(
            "wake word heard%s",
            " (early, still speaking)" if early else (" with a request" if rest else ""),
        )
        self._on_wake(
            check.pcm,
            "" if early else rest,
            lambda: self._audio_after(check.end),
            follow=lambda on_chunk, on_complete=None: self._follow(
                check, on_chunk, on_complete
            ),
            last_speech_at=check.last_speech_at,
        )

    def _check_complete(self, check: _Check) -> None:
        with self._lock:
            following = self._following
        if following is None or following.on_complete is None:
            return
        try:
            text = self._transcriber(check.pcm)
        except Exception:
            logger.exception("wake word transcription failed")
            return
        heard, rest = match_wake_word(text)
        words = rest if heard else text
        if words and self._is_complete(words):
            with self._lock:
                if self._following is not following:
                    return  # the turn ended some other way meanwhile
            logger.info("hands-free turn sounds complete; ending it now")
            following.on_complete()

    # -- handing the mic to a turn ----------------------------------------

    def _audio_after(self, end: int) -> bytes:
        with self._lock:
            return b"".join(chunk for i, chunk in self._recent if i > end)

    def _follow(self, check: _Check, on_chunk, on_complete) -> _Following:
        with self._lock:
            following = _Following(self, on_chunk, on_complete)
            missed = [chunk for i, chunk in self._recent if i > check.end]
            if check.kind == "early":
                # Heard mid-sentence: her request has already begun in
                # this audio ("Saathi ca-"), so it is part of the turn.
                # A name heard as a whole utterance is not -- what she
                # says after it is (and the router drops a leading name
                # anyway).
                on_chunk(check.pcm)
            for chunk in missed:
                on_chunk(chunk)
            # The name's audio too, so a pause in what she says next is
            # judged with the whole request in view ("Saathi call Udhi").
            following.audio = [check.pcm, *missed]
            self._following = following
            return following

    def _unfollow(self, following: _Following) -> None:
        with self._lock:
            if self._following is following:
                self._following = None
                self._segmenter.reset()
                self._last_end = self._index

    def after(self) -> bytes:
        """Audio heard since the last whole utterance ended, while
        listening -- the simpler hand-off `after` in `on_wake` gives."""
        with self._lock:
            return self._audio_after(self._last_end)
