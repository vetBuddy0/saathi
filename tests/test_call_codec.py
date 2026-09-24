"""saathi/call/codec.py — μ-law and resampling on synthetic tones, at
the byte level, with no process or hardware."""

import numpy as np
import pytest

from saathi.call.codec import pcm16_to_ulaw, resample_pcm16, rms, ulaw_to_pcm16


def _tone(freq_hz: float, rate: int, seconds: float, amplitude: float = 0.5) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * freq_hz * t) * amplitude * 32767).astype("<i2").tobytes()


def _dominant_frequency(pcm: bytes, rate: int) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    return float(np.fft.rfftfreq(samples.size, 1 / rate)[np.argmax(spectrum)])


def test_ulaw_round_trip_keeps_a_tone_within_tolerance():
    original = _tone(440, 8000, 0.5)
    decoded = ulaw_to_pcm16(pcm16_to_ulaw(original))
    assert len(decoded) == len(original)
    a = np.frombuffer(original, dtype="<i2").astype(np.float64)
    b = np.frombuffer(decoded, dtype="<i2").astype(np.float64)
    noise = np.sqrt(np.mean((a - b) ** 2))
    signal = np.sqrt(np.mean(a**2))
    # G.711 gives ~38 dB SNR on a full-ish scale sine; assert a
    # comfortable margin below that, not the exact figure.
    assert 20 * np.log10(signal / noise) > 30


def test_ulaw_encode_matches_the_reference_table_on_known_values():
    # Silence is 0xFF in μ-law. -1 is 0x7E, not 0x7F: the reference
    # shifts to 14 bits first (-1 >> 2 is still -1), so the smallest
    # negative lands one code past the negative zero. -4 is the first
    # value that reaches the same magnitude as +1 does.
    assert pcm16_to_ulaw(np.array([0], dtype="<i2").tobytes()) == b"\xff"
    assert pcm16_to_ulaw(np.array([-1], dtype="<i2").tobytes()) == b"\x7e"
    assert pcm16_to_ulaw(np.array([-4], dtype="<i2").tobytes()) == b"\x7e"
    assert pcm16_to_ulaw(np.array([4], dtype="<i2").tobytes()) == b"\xfe"
    # Full-scale positive/negative hit the clip and encode to 0x80/0x00.
    assert pcm16_to_ulaw(np.array([32767], dtype="<i2").tobytes()) == b"\x80"
    assert pcm16_to_ulaw(np.array([-32768], dtype="<i2").tobytes()) == b"\x00"


def test_ulaw_matches_audioop_where_audioop_still_exists():
    # audioop is gone in 3.13 (why this module exists); on interpreters
    # that still have it, use it as an oracle for every input value.
    audioop = pytest.importorskip("audioop")
    every_sample = np.arange(-32768, 32768, dtype="<i2").tobytes()
    assert pcm16_to_ulaw(every_sample) == audioop.lin2ulaw(every_sample, 2)
    every_byte = bytes(range(256))
    assert ulaw_to_pcm16(every_byte) == audioop.ulaw2lin(every_byte, 2)


def test_downsample_16k_to_8k_preserves_the_tone_frequency():
    original = _tone(440, 16000, 0.5)
    down = resample_pcm16(original, 16000, 8000)
    assert len(down) == len(original) // 2
    assert abs(_dominant_frequency(down, 8000) - 440) < 5


def test_upsample_8k_to_16k_preserves_the_tone_frequency():
    original = _tone(440, 8000, 0.5)
    up = resample_pcm16(original, 8000, 16000)
    assert len(up) == len(original) * 2
    assert abs(_dominant_frequency(up, 16000) - 440) < 5


def test_resample_handles_empty_and_same_rate():
    assert resample_pcm16(b"", 16000, 8000) == b""
    data = _tone(300, 8000, 0.01)
    assert resample_pcm16(data, 8000, 8000) == data


def test_rms_of_silence_is_zero_and_of_a_tone_is_its_amplitude_over_root_two():
    assert rms(b"\x00" * 100) == 0.0
    assert rms(b"") == 0.0
    tone = _tone(440, 8000, 1.0, amplitude=0.5)
    assert abs(rms(tone) - 0.5 * 32767 / np.sqrt(2)) < 50
