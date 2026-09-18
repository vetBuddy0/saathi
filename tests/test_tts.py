"""Tests for `saathi/voice/tts/`: the sentence splitter, the
`TTSBackend` interface contract, and each concrete backend's
`available()` — the part of each backend that's meaningful to test
without a GPU, a GCP project, or a Pi. Real synthesis for Piper is
already exercised end-to-end via `tests/test_cascade.py` and
`tests/test_smoke.py`'s barge-in checks; Kokoro and Google's real
synthesis paths need their actual dependencies/credentials and are not
faked here -- see each backend module's docstring for what was verified
by hand instead.
"""

import pytest

from saathi.voice.tts import TTSBackend, split_into_sentences
from saathi.voice.tts.google_backend import GoogleChirp3HDBackend, GoogleNeural2WaveNetBackend
from saathi.voice.tts.kokoro_backend import KokoroBackend
from saathi.voice.tts.melo_backend import MeloTTSBackend
from saathi.voice.tts.piper_backend import PiperBackend
from saathi.voice.tts.registry import DEFAULT_BACKEND_ID, default_backends


def test_split_into_sentences_basic():
    assert split_into_sentences("One. Two. Three.") == ["One.", "Two.", "Three."]


def test_split_into_sentences_handles_question_and_exclamation():
    assert split_into_sentences("Are you well? I hope so!") == ["Are you well?", "I hope so!"]


def test_split_into_sentences_empty_text_is_empty_list():
    assert split_into_sentences("") == []
    assert split_into_sentences("   ") == []


def test_split_into_sentences_single_sentence_no_trailing_punctuation():
    assert split_into_sentences("hello there") == ["hello there"]


def test_split_into_sentences_never_drops_text():
    # A missed split just widens the interrupt window back toward
    # whole-text synthesis -- it must never lose words.
    text = "Dr. Rao will see you at 3.30pm. Please wait outside."
    assert "".join(split_into_sentences(text)).replace(" ", "") == text.replace(" ", "")


def test_registry_includes_piper_and_it_is_always_available():
    backends = default_backends()
    assert DEFAULT_BACKEND_ID in backends
    piper = backends[DEFAULT_BACKEND_ID]
    available, reason = piper.available()
    assert available is True
    assert reason == ""


def test_registry_ids_are_unique_and_match_backend_id_attribute():
    backends = default_backends()
    for key, backend in backends.items():
        assert key == backend.id


def test_piper_backend_is_always_available():
    available, reason = PiperBackend().available()
    assert (available, reason) == (True, "")


def test_melo_backend_is_permanently_unavailable_with_a_stated_reason():
    available, reason = MeloTTSBackend().available()
    assert available is False
    assert "torch" in reason.lower()
    assert "python 3.12" in reason.lower() or "3.12" in reason


def test_melo_backend_raises_if_synthesis_is_attempted_anyway():
    with pytest.raises(RuntimeError):
        list(MeloTTSBackend().synthesize_stream("english", ["hello"]))


def test_kokoro_backend_reports_unavailable_reason_when_not_installed():
    available, reason = KokoroBackend().available()
    # Whether kokoro is actually installed in this environment varies;
    # either state must be a valid (bool, str) pair, never a raise.
    assert isinstance(available, bool)
    assert isinstance(reason, str)


def test_kokoro_backend_rejects_a_language_it_has_no_voice_for():
    backend = KokoroBackend()
    if not backend.available()[0]:
        pytest.skip("kokoro not installed in this environment")
    with pytest.raises(ValueError):
        list(backend.synthesize_stream("bengali", ["hello"]))


def test_google_backends_report_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    for backend in (GoogleNeural2WaveNetBackend(), GoogleChirp3HDBackend()):
        available, reason = backend.available()
        assert available is False
        assert reason  # a real, non-empty explanation, not a bare False


def test_google_backends_are_never_local_and_have_a_nonzero_cost():
    for backend in (GoogleNeural2WaveNetBackend(), GoogleChirp3HDBackend()):
        assert backend.local is False
        assert backend.cost_per_million_chars_usd() > 0.0


def test_local_backends_report_zero_cost():
    for backend_cls in (PiperBackend, KokoroBackend, MeloTTSBackend):
        backend = backend_cls()
        assert backend.local is True
        assert backend.cost_per_million_chars_usd() == 0.0


def test_tts_backend_is_abstract():
    with pytest.raises(TypeError):
        TTSBackend()
