"""The Google backends, made real on 2026-09-25: the per-language voice
tables (the 2026-09-18 draft sent an `en-US-*` voice with every
language code, which the API rejects for Mandarin), the WAV wrapping
that turns Chirp's headerless PCM into the one-WAV-per-sentence
contract, and the exact request shapes, asserted against a fake client.

Everything here runs without credentials. The request-shape tests need
the client library's message types (protobuf, harmless offline) and
skip where the `google-tts` group isn't installed -- CI. The live API
is exercised by `saathi/voice/tts/compare.py`, never by the suite.
"""

from __future__ import annotations

import io
import wave

import pytest

from saathi.voice.tts.google_backend import (
    DEFAULT_CHIRP_SPEAKER,
    GoogleChirp3HDBackend,
    GoogleNeural2WaveNetBackend,
    pcm_to_wav,
)

LANGUAGES = ("english", "chinese", "hindi", "bengali")


def test_google_backends_report_unavailable_when_the_client_library_is_missing(monkeypatch):
    monkeypatch.setattr(
        "saathi.voice.tts.google_backend._client_importable",
        lambda: (False, "google-cloud-texttospeech is not installed"),
    )
    for backend in (GoogleNeural2WaveNetBackend(), GoogleChirp3HDBackend()):
        available, reason = backend.available()
        assert available is False
        assert "not installed" in reason


def test_google_unavailable_reason_names_the_variable_when_unset(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("saathi.voice.tts.google_backend._client_importable", lambda: (True, ""))
    _available, reason = GoogleChirp3HDBackend().available()
    assert "GOOGLE_APPLICATION_CREDENTIALS" in reason


def test_google_unavailable_reason_names_the_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "nope.json"))
    monkeypatch.setattr("saathi.voice.tts.google_backend._client_importable", lambda: (True, ""))
    available, reason = GoogleChirp3HDBackend().available()
    assert available is False
    assert "nope.json" in reason


def test_pcm_to_wav_is_a_real_mono_16bit_wav_at_the_given_rate():
    pcm = b"\x01\x00\xff\xff" * 100  # 200 frames
    wav_bytes = pcm_to_wav(pcm, 24000)
    assert wav_bytes[:4] == b"RIFF" and wav_bytes[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24000
        assert wav_file.getnframes() == 200
        assert wav_file.readframes(200) == pcm


def test_chirp_voice_is_the_same_speaker_in_every_language():
    # The whole reason Chirp3-HD matters for "recognisably the same
    # character": one speaker name, every language code.
    backend = GoogleChirp3HDBackend()
    voices = {lang: backend.voice_for(lang) for lang in LANGUAGES}
    assert voices["english"] == (f"en-US-Chirp3-HD-{DEFAULT_CHIRP_SPEAKER}", "en-US")
    assert voices["chinese"] == (f"cmn-CN-Chirp3-HD-{DEFAULT_CHIRP_SPEAKER}", "cmn-CN")
    assert {name.rsplit("-", 1)[1] for name, _code in voices.values()} == {DEFAULT_CHIRP_SPEAKER}


def test_chirp_speaker_is_configurable_for_the_candidate_shortlist():
    assert GoogleChirp3HDBackend(speaker="Gacrux").voice_for("chinese")[0] == (
        "cmn-CN-Chirp3-HD-Gacrux"
    )


def test_neural2_backend_voice_matches_the_language_code_it_sends():
    backend = GoogleNeural2WaveNetBackend()
    for language in LANGUAGES:
        name, code = backend.voice_for(language)
        assert name.startswith(code + "-"), (name, code)


def test_neural2_backend_uses_wavenet_where_google_has_no_neural2_voice():
    # cmn-CN and bn-IN have no Neural2 voices at all (list_voices(),
    # Singapore endpoint, 2026-09-25) -- WaveNet is the honest fallback
    # within the same class, not a fabricated voice name.
    backend = GoogleNeural2WaveNetBackend()
    assert "Wavenet" in backend.voice_for("chinese")[0]
    assert "Wavenet" in backend.voice_for("bengali")[0]
    assert "Neural2" in backend.voice_for("english")[0]


def test_google_backends_reject_an_unknown_language_rather_than_defaulting_to_english():
    for backend in (GoogleNeural2WaveNetBackend(), GoogleChirp3HDBackend()):
        with pytest.raises(ValueError):
            backend.voice_for("klingon")
        with pytest.raises(ValueError):
            list(backend.synthesize_stream("klingon", ["hello"]))


class _FakeResponse:
    def __init__(self, audio_content: bytes) -> None:
        self.audio_content = audio_content


class _FakeGoogleClient:
    """Stands in for `TextToSpeechClient`: records every request, answers
    streaming with a fixed sequence of PCM chunks and batch with a WAV."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = chunks or [b"\x01\x00" * 4800, b"\x02\x00" * 5760]
        self.streaming_requests: list[list] = []
        self.batch_calls: list[dict] = []
        self.timeouts: list = []

    def streaming_synthesize(self, requests, timeout=None):
        self.streaming_requests.append(list(requests))
        self.timeouts.append(timeout)
        for chunk in self.chunks:
            yield _FakeResponse(chunk)

    def synthesize_speech(self, *, input, voice, audio_config, timeout=None):
        self.batch_calls.append({"input": input, "voice": voice, "audio_config": audio_config})
        self.timeouts.append(timeout)
        return _FakeResponse(pcm_to_wav(b"".join(self.chunks), 24000))


class _FailingGoogleClient(_FakeGoogleClient):
    """Every call raises, the way a dropped network, a revoked key or an
    exhausted quota surfaces from the real client."""

    def streaming_synthesize(self, requests, timeout=None):
        list(requests)
        raise RuntimeError("503 unavailable")
        yield  # pragma: no cover -- makes this a generator like the real one

    def synthesize_speech(self, **_kwargs):
        raise RuntimeError("503 unavailable")


def test_chirp_streaming_request_shape_and_one_wav_per_sentence():
    texttospeech = pytest.importorskip("google.cloud.texttospeech_v1")
    client = _FakeGoogleClient()
    backend = GoogleChirp3HDBackend(client=client)

    wavs = list(backend.synthesize_stream("chinese", ["一。", "二。"]))

    assert len(wavs) == 2
    assert len(client.streaming_requests) == 2  # one stream per sentence (DECISIONS 2026-09-18)
    config_request, text_request = client.streaming_requests[0]
    # Config first, then text -- the API's required order.
    assert config_request.streaming_config.voice.name == f"cmn-CN-Chirp3-HD-{DEFAULT_CHIRP_SPEAKER}"
    assert config_request.streaming_config.voice.language_code == "cmn-CN"
    audio_config = config_request.streaming_config.streaming_audio_config
    assert audio_config.audio_encoding == texttospeech.AudioEncoding.PCM
    assert audio_config.sample_rate_hertz == 24000
    assert text_request.input.text == "一。"
    for wav_bytes in wavs:  # each item: a complete WAV wrapping every chunk of that sentence
        assert wav_bytes[:4] == b"RIFF"
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            assert wav_file.getframerate() == 24000
            assert wav_file.getnframes() == 4800 + 5760


def test_chirp_synthesize_stream_is_lazy_per_sentence():
    pytest.importorskip("google.cloud.texttospeech_v1")
    client = _FakeGoogleClient()
    stream = GoogleChirp3HDBackend(client=client).synthesize_stream("english", ["One.", "Two."])
    assert client.streaming_requests == []  # nothing until asked
    next(stream)
    assert len(client.streaming_requests) == 1  # the second sentence hasn't been paid for yet


def test_chirp_stream_pcm_yields_raw_chunks_as_they_arrive():
    pytest.importorskip("google.cloud.texttospeech_v1")
    client = _FakeGoogleClient()
    chunks = list(GoogleChirp3HDBackend(client=client).stream_pcm("english", "Hello."))
    assert chunks == client.chunks  # raw, unwrapped, in order


def test_neural2_batch_request_shape_and_wav_passthrough():
    texttospeech = pytest.importorskip("google.cloud.texttospeech_v1")
    client = _FakeGoogleClient()
    backend = GoogleNeural2WaveNetBackend(client=client)

    wavs = list(backend.synthesize_stream("chinese", ["一。", "二。"]))

    assert len(client.batch_calls) == 2  # one blocking call per sentence, lazily
    call = client.batch_calls[0]
    assert call["input"].text == "一。"
    assert call["voice"].name == "cmn-CN-Wavenet-A"
    assert call["voice"].language_code == "cmn-CN"
    assert call["audio_config"].audio_encoding == texttospeech.AudioEncoding.LINEAR16
    assert call["audio_config"].sample_rate_hertz == 24000
    assert all(w[:4] == b"RIFF" for w in wavs)


def test_neural2_wraps_headerless_audio_if_google_ever_stops_sending_a_wav():
    pytest.importorskip("google.cloud.texttospeech_v1")

    class HeaderlessClient(_FakeGoogleClient):
        def synthesize_speech(self, **_kwargs):
            return _FakeResponse(b"\x01\x00" * 100)

    wav_bytes = next(
        GoogleNeural2WaveNetBackend(client=HeaderlessClient()).synthesize_stream(
            "english", ["Hi."]
        )
    )
    assert wav_bytes[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnframes() == 100


def test_every_google_call_carries_a_deadline():
    # A stalled connection must not hold a sentence -- and the turn --
    # open indefinitely. Review finding, 2026-09-25.
    pytest.importorskip("google.cloud.texttospeech_v1")
    for backend_cls in (GoogleChirp3HDBackend, GoogleNeural2WaveNetBackend):
        client = _FakeGoogleClient()
        list(backend_cls(client=client).synthesize_stream("english", ["Hi."]))
        assert client.timeouts == [10.0]


@pytest.fixture
def credentials_present(monkeypatch, tmp_path):
    key = tmp_path / "gcp.json"
    key.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    monkeypatch.setattr("saathi.voice.tts.google_backend._client_importable", lambda: (True, ""))


@pytest.mark.parametrize("backend_cls", [GoogleChirp3HDBackend, GoogleNeural2WaveNetBackend])
def test_a_failed_sentence_degrades_to_silence_and_takes_the_backend_offline_for_a_while(
    credentials_present, backend_cls, caplog
):
    # Why silence and not a raise: cascade's prefetch thread only
    # enqueues on success, so a raise here would leave _speak() blocked
    # on its queue forever -- a dead session, not a lost sentence. The
    # cooldown routes the *next* turn to Piper via the existing
    # _current_backend() fallback.
    pytest.importorskip("google.cloud.texttospeech_v1")
    now = [1000.0]
    backend = backend_cls(client=_FailingGoogleClient(), clock=lambda: now[0])
    assert backend.available() == (True, "")

    with caplog.at_level("WARNING"):
        wavs = list(backend.synthesize_stream("english", ["One.", "Two."]))

    assert len(wavs) == 2 and all(w[:4] == b"RIFF" for w in wavs)  # the turn completes
    with wave.open(io.BytesIO(wavs[0]), "rb") as wav_file:
        assert wav_file.getnframes() == 2400  # 100 ms of silence at 24 kHz
    assert "503 unavailable" in caplog.text  # never silent in the log

    available, reason = backend.available()
    assert available is False
    assert "503 unavailable" in reason and "retrying" in reason

    now[0] += 61.0  # cooldown over: eligible again
    assert backend.available() == (True, "")


def test_a_success_after_a_failure_clears_the_cooldown(credentials_present):
    pytest.importorskip("google.cloud.texttospeech_v1")
    now = [0.0]
    backend = GoogleChirp3HDBackend(client=_FailingGoogleClient(), clock=lambda: now[0])
    list(backend.synthesize_stream("english", ["Hi."]))
    assert backend.available()[0] is False

    backend._client = _FakeGoogleClient()  # the network came back
    now[0] += 61.0
    list(backend.synthesize_stream("english", ["Hi."]))
    assert backend.available() == (True, "")
