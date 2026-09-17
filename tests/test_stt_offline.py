"""Local STT (`faster-whisper`) is the privacy fallback the Triggers
section promises: once cascade sends audio off-device, local STT is "the
way back" if that ever needs to stop. A fallback nobody has run without
network access isn't a fallback, it's an assumption — this proves the
already-cached model transcribes with the network cut off, not just that
it *should*.

Skipped unless the `hardware` dependency group is installed (`uv sync
--group hardware`) — CI doesn't install it, same reason `saathi smoke
--aec` doesn't run there. This needs a machine where the model has been
downloaded once, not a headless runner.
"""

import socket
from pathlib import Path

import pytest

faster_whisper = pytest.importorskip("faster_whisper")

_KNOWN_SENTENCE_WAV = (
    Path(__file__).parent.parent / "saathi" / "audio" / "testdata" / "known_sentence.wav"
)


def test_faster_whisper_transcribes_with_the_network_unavailable(monkeypatch):
    # Belt: tell every HF-aware library to not even try the network.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # Suspenders: if something tries anyway, fail loudly instead of
    # silently succeeding over a network call this test was supposed to
    # rule out.
    def _no_network(*_args, **_kwargs):
        raise OSError("network access attempted during an offline STT test")

    monkeypatch.setattr(socket, "socket", _no_network)

    model = faster_whisper.WhisperModel("tiny.en", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(_KNOWN_SENTENCE_WAV), beam_size=5)
    transcript = " ".join(segment.text for segment in segments).strip().lower()

    # Not just "didn't crash" — it actually has to have transcribed
    # something recognisable from the known fixture.
    assert "weather" in transcript
