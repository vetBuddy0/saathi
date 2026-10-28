"""The developer's switch for keeping what the transcriber heard: off
unless the flag file exists, and only the newest few turns when on. No
real Groq/OpenAI, TTS or audio: the same fakes as test_cascade.py."""

import wave
from types import SimpleNamespace

import pytest

import saathi.voice.engine.cascade as cascade_module
from saathi.voice.engine.cascade import (
    KEEP_TURN_AUDIO_COUNT,
    KEEP_TURN_AUDIO_FLAG,
    CascadeSession,
)
from tests.test_cascade import FakeClient, FakeTTSBackend, _hears_speech


@pytest.fixture(autouse=True)
def no_real_playback(monkeypatch):
    monkeypatch.setattr(
        cascade_module, "play", lambda sink_id, path: SimpleNamespace(wait=lambda: None)
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_DATA_DIR", str(tmp_path))
    # The keeping runs synchronously, so the test reads the file the turn
    # wrote; every other thread is left real.
    real_thread = cascade_module.threading.Thread

    def thread(*args, target=None, **kwargs):
        if target is cascade_module._keep_turn_audio:
            return SimpleNamespace(start=lambda: target(*kwargs["args"]))
        return real_thread(*args, target=target, **kwargs)

    monkeypatch.setattr(cascade_module.threading, "Thread", thread)
    return tmp_path


def _turn(pcm: bytes) -> str:
    session = CascadeSession(
        "fake-sink",
        speech_gate=_hears_speech,
        client=FakeClient(heard="I'm feeling low today."),
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
    )
    session.start()
    session.send_audio(pcm)
    return session.end_turn()


def test_no_flag_keeps_nothing(data_dir):
    _turn(b"\x10\x00" * 1600)
    assert not (data_dir / "debug").exists()


def test_with_the_flag_the_exact_audio_is_kept_and_only_the_newest_turns(data_dir, monkeypatch):
    debug = data_dir / "debug"
    debug.mkdir()
    (debug / KEEP_TURN_AUDIO_FLAG).touch()
    for n in range(KEEP_TURN_AUDIO_COUNT + 2):
        (debug / f"turn-20260101-0000{n:02d}.wav").write_bytes(b"old")
    pcm = b"\x10\x00\xf0\xff" * 800
    _turn(pcm)
    kept = sorted(debug.glob("turn-*.wav"))
    assert len(kept) == KEEP_TURN_AUDIO_COUNT
    with wave.open(str(kept[-1])) as clip:
        assert clip.getframerate() == 16000 and clip.getnchannels() == 1
        assert clip.readframes(clip.getnframes()) == pcm
