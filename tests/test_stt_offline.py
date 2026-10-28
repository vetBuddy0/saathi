"""Local STT (`faster-whisper`) is the privacy fallback the Triggers
section promises: once cascade sends audio off-device, local STT is "the
way back" if that ever needs to stop. A fallback nobody has run without
network access isn't a fallback, it's an assumption — this proves the
already-cached model transcribes with the network cut off, not just that
it *should*.

Needs a machine where the `tiny.en` model has been downloaded once (the
wake word does that on first run); `faster-whisper` itself is a default
dependency since the wake word (audio/wake.py).
"""

import socket
from pathlib import Path

import pytest

faster_whisper = pytest.importorskip("faster_whisper")

from huggingface_hub import try_to_load_from_cache  # noqa: E402  (after the skip)

if not isinstance(try_to_load_from_cache("Systran/faster-whisper-tiny.en", "model.bin"), str):
    pytest.skip("tiny.en has never been downloaded on this machine", allow_module_level=True)

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
