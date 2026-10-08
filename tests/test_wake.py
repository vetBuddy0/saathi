"""The "Kaki" wake word: matching, cutting utterances, ending a
hands-free turn, and the listener gluing them -- all with fake speech
decisions, so no model or microphone is needed."""

import pytest

from saathi.audio.vad import CHUNK_BYTES
from saathi.audio.wake import Endpointer, UtteranceSegmenter, WakeWordListener, match_wake_word

SPEECH = b"\x01" * CHUNK_BYTES
QUIET = b"\x00" * CHUNK_BYTES


def _is_speech(chunk: bytes) -> bool:
    return chunk[:1] == b"\x01"


# -- matching ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, rest",
    [
        # Spellings whisper actually produced for her name (2026-10-07).
        ("Kaki", ""),
        ("Kaki.", ""),
        ("Hey Kaki!", ""),
        ("Kaki, play a song.", "play a song"),
        ("Khaki, call a Thai.", "call a thai"),
        ("Ka ki, play a song.", "play a song"),
        ("Kakki, what's the time?", "what s the time"),
        ("Kaki?", ""),
    ],
)
def test_her_name_is_heard_with_what_follows(text, rest):
    assert match_wake_word(text) == (True, rest)


@pytest.mark.parametrize(
    "text",
    [
        "Sorry about that.",
        "Satisfied",
        "That's it!",
        "Hello there!",
        "Kai is here",  # 0.86 -- under the line
        "My kopi kaki came today",  # a friend, not a call
        "Mahjong kaki tonight",
        "Khakis are on sale",  # 0.80
        "I was telling my friend about kaki",  # the name, but not a call
        "",
    ],
)
def test_ordinary_speech_is_not_a_call(text):
    assert match_wake_word(text) == (False, "")


# -- utterances ------------------------------------------------------------


def test_an_utterance_runs_from_speech_to_the_pause_with_a_little_before_it():
    seg = UtteranceSegmenter(_is_speech, preroll_seconds=0.064, silence_seconds=0.096)
    out = [seg.push(c) for c in [QUIET, QUIET, QUIET, SPEECH, SPEECH, QUIET, QUIET, QUIET]]
    done = [o for o in out if o is not None]
    assert len(done) == 1
    # two chunks of preroll, two of speech, three of trailing quiet
    assert done[0] == QUIET * 2 + SPEECH * 2 + QUIET * 3
    assert out[-1] is not None  # emitted on the third quiet chunk


def test_a_long_stretch_of_speech_is_dropped_not_transcribed():
    seg = UtteranceSegmenter(_is_speech, silence_seconds=0.064, max_seconds=0.32)
    out = [seg.push(SPEECH) for _ in range(20)] + [seg.push(QUIET) for _ in range(3)]
    assert all(o is None for o in out)
    # and the next short one is still heard
    out = [seg.push(SPEECH), seg.push(QUIET), seg.push(QUIET)]
    assert out[-1] is not None


# -- ending a hands-free turn ----------------------------------------------


def test_the_turn_ends_after_she_speaks_and_pauses():
    ep = Endpointer(_is_speech, silence_seconds=0.096, no_speech_seconds=10)
    assert not ep.push(QUIET * 5 + SPEECH * 10 + QUIET * 2)
    assert ep.push(QUIET)


def test_the_turn_ends_if_she_says_nothing():
    ep = Endpointer(_is_speech, no_speech_seconds=0.32)
    assert not ep.push(QUIET * 9)
    assert ep.push(QUIET)


def test_a_turn_never_runs_past_its_limit():
    ep = Endpointer(_is_speech, max_seconds=0.32)
    assert not ep.push(SPEECH * 9)
    assert ep.push(SPEECH)


def test_the_endpointer_takes_capture_sized_chunks():
    # capture delivers 3200-byte chunks; VAD wants CHUNK_BYTES.
    ep = Endpointer(_is_speech, silence_seconds=0.096, no_speech_seconds=10)
    assert not ep.push(SPEECH * 4)
    assert ep.push(b"\x00" * 3200)


# -- the listener -------------------------------------------------------------


class _Capture:
    def __init__(self, source_id, on_chunk, chunk_bytes):
        self.on_chunk = on_chunk
        self.chunk_bytes = chunk_bytes

    def start(self):
        pass

    def stop(self):
        pass


def _listener(transcript, listening=True):
    woke = []
    calls = []

    def transcriber(pcm):
        calls.append(pcm)
        return transcript

    listener = WakeWordListener(
        "src",
        lambda pcm, rest, after, **handover: woke.append((pcm, rest)),
        lambda: listening,
        transcriber=transcriber,
        is_speech=_is_speech,
        capture_factory=_Capture,
    )
    return listener, woke, calls


def test_the_listener_wakes_on_her_name_and_passes_the_request_along():
    listener, woke, _ = _listener("Kaki, play a song")
    listener.check(SPEECH)
    assert woke == [(SPEECH, "play a song")]


def test_the_listener_ignores_other_speech():
    listener, woke, calls = _listener("Hello there")
    listener.check(SPEECH)
    assert woke == [] and len(calls) == 1


def test_nothing_is_transcribed_while_the_device_is_busy():
    listener, woke, calls = _listener("Kaki", listening=False)
    listener.check(SPEECH)
    assert woke == [] and calls == []


def test_the_listener_reads_vad_sized_chunks_from_the_mic():
    listener, _, _ = _listener("Kaki")
    listener.start()
    try:
        assert listener._capture.chunk_bytes == CHUNK_BYTES
    finally:
        listener.stop()


def test_what_she_says_after_the_name_is_kept_for_the_turn():
    listener, _, _ = _listener("Kaki")
    listener._segmenter = UtteranceSegmenter(_is_speech, silence_seconds=0.064)
    for chunk in [SPEECH, QUIET, QUIET]:  # "Kaki", then the pause ends it
        listener._on_chunk(chunk)
    assert listener.after() == b""
    listener._on_chunk(SPEECH)  # "play..." while the name is being checked
    assert listener.after() == SPEECH


def test_audio_after_the_name_is_dropped_once_the_device_is_busy():
    state = {"listening": True}
    listener = WakeWordListener(
        "src",
        lambda *a: None,
        lambda: state["listening"],
        transcriber=lambda pcm: "",
        is_speech=_is_speech,
        capture_factory=_Capture,
    )
    listener._on_chunk(SPEECH)
    state["listening"] = False
    listener._on_chunk(SPEECH)
    assert listener.after() == b""


# -- hearing the name early, and handing the mic to the turn (2026-10-07) ----


def test_khaki_is_her_name():
    # Spelled like the trousers, still her name (0.89, over the line).
    assert match_wake_word("Khaki, call Udi.") == (True, "call udi")


def _drain(listener):
    """Run whatever the mic thread queued, as the worker would."""
    while not listener._queue.empty():
        item = listener._queue.get_nowait()
        if item is not None:
            listener._run_check(item)


def _early_listener(transcripts, *, is_complete=None, early=(0.064,)):
    """A listener whose transcriber answers from `transcripts` in turn."""
    woke = []
    state = {"listening": True}
    answers = list(transcripts)

    def transcriber(pcm):
        return answers.pop(0) if answers else ""

    def on_wake(pcm, rest, after, follow=None, last_speech_at=None):
        woke.append({"pcm": pcm, "rest": rest, "follow": follow})
        state["listening"] = False

    listener = WakeWordListener(
        "src",
        on_wake,
        lambda: state["listening"],
        transcriber=transcriber,
        is_speech=_is_speech,
        capture_factory=_Capture,
        is_complete=is_complete,
        early_check_seconds=early,
    )
    listener._segmenter = UtteranceSegmenter(
        _is_speech, preroll_seconds=0.032, silence_seconds=0.064
    )
    return listener, woke, state


def test_the_name_is_heard_while_she_is_still_speaking():
    listener, woke, _ = _early_listener(["Kaki call"])
    listener._on_chunk(SPEECH)
    listener._on_chunk(SPEECH)  # 2 chunks since onset = 0.064 s: early check
    assert len(woke) == 0  # queued, not yet transcribed
    _drain(listener)
    assert len(woke) == 1
    assert woke[0]["rest"] == ""  # still talking: the request isn't whole yet
    assert woke[0]["follow"] is not None


def test_following_hands_over_every_chunk_after_the_name_in_order():
    listener, woke, _ = _early_listener(["Kaki call"])
    a, b, c, d = (bytes([1, n]) + b"\x00" * (CHUNK_BYTES - 2) for n in range(4))
    listener._on_chunk(a)
    listener._on_chunk(b)  # early check queued, covering a+b
    listener._on_chunk(c)  # arrives while the name is being checked
    _drain(listener)
    turn = []
    handle = woke[0]["follow"](turn.append)
    listener._on_chunk(d)
    # The name's audio (heard mid-sentence, so part of the request),
    # then what came while it was checked, then the live mic.
    assert turn == [a + b, c, d]
    handle.stop()
    listener._on_chunk(SPEECH)
    assert turn == [a + b, c, d]  # taken back: nothing more forwarded


def test_an_early_wake_is_not_repeated_when_the_utterance_ends():
    listener, woke, state = _early_listener(["Kaki call", "Kaki call Udhi"])
    listener._on_chunk(SPEECH)
    listener._on_chunk(SPEECH)
    _drain(listener)
    state["listening"] = True  # as if the turn had already ended
    for chunk in [SPEECH, QUIET, QUIET]:
        listener._on_chunk(chunk)
    _drain(listener)
    assert len(woke) == 1


def test_no_early_check_while_the_transcriber_is_busy():
    listener, woke, _ = _early_listener(["Kaki"])
    listener._busy = True
    listener._on_chunk(SPEECH)
    listener._on_chunk(SPEECH)
    assert listener._queue.empty()


def test_a_name_heard_alone_is_not_part_of_the_turn():
    listener, woke, _ = _early_listener(["Hello", "Kaki"], early=(1.0,))
    for chunk in [SPEECH, SPEECH, QUIET, QUIET]:
        listener._on_chunk(chunk)
    _drain(listener)  # "Hello": no wake
    assert woke == []
    listener._busy = False
    listener._woken_by = -1
    for chunk in [SPEECH, QUIET, QUIET]:
        listener._on_chunk(chunk)
    _drain(listener)
    assert len(woke) == 1 and woke[0]["rest"] == ""
    turn = []
    woke[0]["follow"](turn.append)
    listener._on_chunk(SPEECH)
    assert turn == [SPEECH]  # what she says next, not the name


def test_a_followed_turn_that_is_already_a_command_ends_at_the_short_pause():
    completed = []
    listener, woke, _ = _early_listener(
        ["Kaki call", "Kaki call Udhi"], is_complete=lambda words: words == "call udhi"
    )
    listener._on_chunk(SPEECH)
    listener._on_chunk(SPEECH)
    _drain(listener)
    woke[0]["follow"](lambda chunk: None, lambda: completed.append(True))
    for chunk in [SPEECH, QUIET, QUIET]:  # "...Udhi", then a short pause
        listener._on_chunk(chunk)
    _drain(listener)
    assert completed == [True]


def test_a_followed_turn_that_is_not_a_command_waits_for_the_endpointer():
    completed = []
    listener, woke, _ = _early_listener(
        ["Kaki call", "Kaki call my"], is_complete=lambda words: False
    )
    listener._on_chunk(SPEECH)
    listener._on_chunk(SPEECH)
    _drain(listener)
    woke[0]["follow"](lambda chunk: None, lambda: completed.append(True))
    for chunk in [SPEECH, QUIET, QUIET]:
        listener._on_chunk(chunk)
    _drain(listener)
    assert completed == []


def test_the_endpointer_remembers_when_it_last_heard_her():
    times = iter([1.0, 2.0, 3.0])
    ep = Endpointer(_is_speech, silence_seconds=0.5, clock=lambda: next(times))
    assert ep.last_speech_at is None
    ep.push(QUIET + SPEECH + QUIET + SPEECH + QUIET)
    assert ep.last_speech_at == 2.0


@pytest.mark.parametrize(
    "text, stripped",
    [
        ("Khaki, call Udhi.", "call Udhi."),
        ("Kaki call Udi", "call Udi"),
        ("Kakki Kaul Udi.", "Kaul Udi."),
        ("Ka ki, play a song.", "play a song."),
        ("call Kaki", "call Kaki"),  # her name as the object stays
        ("Sorry about that", "Sorry about that"),
        ("", ""),
    ],
)
def test_a_leading_name_is_stripped(text, stripped):
    from saathi.audio.wake import strip_wake_word

    assert strip_wake_word(text) == stripped
