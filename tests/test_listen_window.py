"""audio/listen_window.py: the open mic after a reply. Fed synthetic
chunks with an injected speech detector (audio/vad.py's own tests cover
Silero itself)."""

from saathi.audio.listen_window import ListenWindow
from saathi.audio.vad import CHUNK_BYTES, SAMPLE_RATE
from saathi.audio.wake import Endpointer

QUIET = b"\x00" * CHUNK_BYTES
SPEECH = b"\x01" * CHUNK_BYTES
CHUNK_SECONDS = CHUNK_BYTES / 2 / SAMPLE_RATE


def _is_speech(chunk: bytes) -> bool:
    return chunk[:1] == b"\x01"


def _window(**kwargs) -> ListenWindow:
    kwargs.setdefault("endpointer", Endpointer(_is_speech, silence_seconds=0.2))
    return ListenWindow(_is_speech, **kwargs)


def test_a_quiet_window_lapses_and_nothing_starts():
    window = _window(window_seconds=1.0, guard_seconds=0.0)
    events = [window.push(QUIET) for _ in range(round(1.0 / CHUNK_SECONDS) + 2)]
    assert "start" not in events
    assert events.count("lapse") == 1
    assert window.lapsed and not window.started


def test_speech_starts_the_turn_with_its_onset_and_a_little_before():
    window = _window(guard_seconds=0.0, onset_chunks=3)
    for _ in range(20):
        assert window.push(QUIET) is None
    assert window.push(SPEECH) is None
    assert window.push(SPEECH) is None
    assert window.push(SPEECH) == "start"
    audio = window.started_audio()
    assert audio.endswith(SPEECH * 3)
    assert QUIET in audio  # the preroll: her first syllable isn't clipped


def test_one_click_is_not_her_starting_to_talk():
    window = _window(guard_seconds=0.0, onset_chunks=3)
    for _ in range(10):
        assert window.push(SPEECH + QUIET) is None
    assert not window.started


def test_her_own_voice_tail_in_the_guard_is_never_heard():
    # The last of Saathi's own reply arriving just after say() returned.
    guard = 0.35
    window = _window(guard_seconds=guard, onset_chunks=3)
    tail_chunks = int(guard / CHUNK_SECONDS)
    for _ in range(tail_chunks):
        assert window.push(SPEECH) is None
    assert not window.started
    assert window.push(QUIET) is None
    assert not window.started


def test_after_she_starts_the_endpointer_ends_it():
    window = _window(guard_seconds=0.0, onset_chunks=2)
    window.push(SPEECH)
    assert window.push(SPEECH) == "start"
    assert window.push(SPEECH) is None
    events = [window.push(QUIET) for _ in range(10)]
    assert "end" in events
    assert window.ended


def test_audio_arriving_in_capture_sized_pieces_works_the_same():
    window = _window(guard_seconds=0.1, onset_chunks=3)
    stream = QUIET * 10 + SPEECH * 6
    events = [window.push(stream[i : i + 3200]) for i in range(0, len(stream), 3200)]
    assert "start" in events
