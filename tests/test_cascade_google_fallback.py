"""The offline guarantee for the Google voices, as a test rather than a
manual check: with no `GOOGLE_APPLICATION_CREDENTIALS`, both Google
backends say why they're unavailable, and a `CascadeSession` whose
stored preference names one of them still speaks -- through Piper --
instead of losing the turn. Uses the real registry, so the real
`available()` gate is what's exercised; only Piper's entry is swapped
for a recording stand-in (same convention as `tests/test_cascade.py`)
so nothing loads a voice model.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Iterator

import pytest

import saathi.voice.engine.cascade as cascade_module
from saathi.voice.engine.cascade import CascadeSession
from saathi.voice.tts import TTSBackend
from saathi.voice.tts.registry import DEFAULT_BACKEND_ID, default_backends

GOOGLE_IDS = ("google-neural2", "google-chirp3-hd")


class RecordingPiperStandIn(TTSBackend):
    id = DEFAULT_BACKEND_ID
    display_name = "Piper stand-in"
    license = "n/a"
    local = True

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    def available(self) -> tuple[bool, str]:
        return True, ""

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        for sentence in sentences:
            self.spoken.append((language, sentence))
            yield b"\x00\x00" * 10

    def cost_per_million_chars_usd(self) -> float:
        return 0.0


@pytest.fixture
def no_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(
        cascade_module, "play", lambda sink_id, path: SimpleNamespace(wait=lambda: None)
    )


@pytest.mark.parametrize("google_id", GOOGLE_IDS)
def test_google_backends_in_the_real_registry_are_unavailable_with_a_reason(
    no_credentials, google_id
):
    available, reason = default_backends()[google_id].available()
    assert available is False
    assert reason  # says why, never a bare False


@pytest.mark.parametrize("google_id", GOOGLE_IDS)
def test_session_preferring_a_google_backend_still_speaks_via_piper(no_credentials, google_id):
    backends = default_backends()
    piper = RecordingPiperStandIn()
    backends[DEFAULT_BACKEND_ID] = piper
    session = CascadeSession(
        "fake-sink",
        client=object(),
        backends=backends,
        backend_preference=lambda: google_id,
        speech_gate=lambda pcm: True,
    )

    assert session._current_backend() is piper
    session.say("Good morning. Did you sleep well?")
    assert piper.spoken == [
        ("english", "Good morning."),
        ("english", "Did you sleep well?"),
    ]
