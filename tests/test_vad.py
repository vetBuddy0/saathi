"""Real Silero VAD (the bundled model, no network, no real hardware —
just the vendored `known_sentence.wav` fixture already used by the AEC
hardware check) against true digital silence, plus `SpeechStartDetector`'s
debounce behaviour on a synthetic silence-then-speech sequence.
"""

import wave
from pathlib import Path

import numpy as np

from saathi.audio.vad import CHUNK_BYTES, SAMPLE_RATE, SpeechStartDetector, VoiceActivityDetector

_KNOWN_SENTENCE_WAV = (
    Path(__file__).parent.parent / "saathi" / "audio" / "testdata" / "known_sentence.wav"
)


def _resampled_pcm16(path: Path, target_rate: int) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        source_rate = wav_file.getframerate()
        data = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype=np.int16)
    if source_rate != target_rate:
        new_length = int(len(data) * target_rate / source_rate)
        index = np.linspace(0, len(data) - 1, new_length)
        data = np.interp(index, np.arange(len(data)), data).astype(np.int16)
    return data.tobytes()


def _chunks(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + CHUNK_BYTES] for i in range(0, len(pcm) - CHUNK_BYTES + 1, CHUNK_BYTES)]


def _speech_chunks() -> list[bytes]:
    return _chunks(_resampled_pcm16(_KNOWN_SENTENCE_WAV, SAMPLE_RATE))


def test_vad_recognizes_real_speech():
    vad = VoiceActivityDetector()
    probabilities = [vad.probability(chunk) for chunk in _speech_chunks()]
    assert max(probabilities) > 0.9
    assert sum(p > 0.5 for p in probabilities) / len(probabilities) > 0.5


def test_vad_does_not_flag_digital_silence():
    vad = VoiceActivityDetector()
    silent_chunk = bytes(CHUNK_BYTES)
    probabilities = [vad.probability(silent_chunk) for _ in range(20)]
    assert max(probabilities) < 0.1


def test_speech_start_detector_ignores_silence_and_fires_once_during_speech():
    vad = VoiceActivityDetector()
    detector = SpeechStartDetector(vad, consecutive_chunks=3)
    silent_chunk = bytes(CHUNK_BYTES)

    silence_fires = [detector.push(silent_chunk) for _ in range(10)]
    assert not any(silence_fires)

    speech_fires = [detector.push(chunk) for chunk in _speech_chunks()]
    assert sum(speech_fires) == 1


def test_speech_start_detector_reset_allows_firing_again():
    vad = VoiceActivityDetector()
    detector = SpeechStartDetector(vad, consecutive_chunks=2)
    chunks = _speech_chunks()

    first_fires = [detector.push(chunk) for chunk in chunks]
    assert sum(first_fires) == 1

    detector.reset()
    second_fires = [detector.push(chunk) for chunk in chunks]
    assert sum(second_fires) == 1
