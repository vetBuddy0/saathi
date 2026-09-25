"""Byte-level conversions between what the mic/speaker speak (16 kHz
PCM16) and what Twilio Media Streams speak (8 kHz G.711 μ-law).

Exists as its own module so the audio path is testable on synthetic
tones with no process, no socket and no hardware — a wrong μ-law bias
or a resample that shifts pitch is a bug a round-trip test catches in
milliseconds and a live call catches only as "she sounds strange".

Contested: `audioop` (stdlib) does all of this in C and works on the
3.12 this project runs today — but it is *removed* in Python 3.13 and
`pyproject.toml` says `>=3.12`, so a device updated to 3.13 would lose
calling at import time. A resampling library would be a new dependency
for ~40 lines. numpy is already a dependency, G.711 is a page of the
standard, and telephone audio doesn't need better than a 2-tap
decimator and linear interpolation. So: numpy, here, durable.

Every function takes and returns `bytes` (little-endian signed 16-bit
for PCM, one byte per sample for μ-law) so callers never touch numpy.
"""

from __future__ import annotations

import numpy as np

_BIAS = 0x84
_BIAS_14 = _BIAS >> 2  # 33
_CLIP_14 = 8159

TELEPHONE_RATE = 8000
DEVICE_RATE = 16000


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    """G.711 μ-law encode, bit-exact with the reference implementation
    (Sun's g711.c, which CPython's `audioop` wrapped): the 16-bit sample
    is arithmetic-shifted to 14 bits *first*, then negated — so a
    negative sample's low two bits round toward negative infinity, not
    away from zero. Doing it on 16 bits and dividing later differs on
    381 of 65536 inputs by one code; inaudible, but "matches the
    reference" is a stronger test than "sounds fine"."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.int32) >> 2
    sign = (samples < 0).astype(np.int32)
    magnitude = np.minimum(np.where(samples < 0, -samples, samples), _CLIP_14) + _BIAS_14
    # The reference's clip leaves 8192 reachable (8159 + 33), which falls
    # off its segment table and is returned as the top code; capping at
    # 8191 yields that same top code (segment 7, mantissa 15) directly.
    magnitude = np.minimum(magnitude, 0x1FFF)
    # floor(log2(magnitude)) exactly, via frexp: magnitude = m * 2**e with
    # m in [0.5, 1), so the top set bit is at e - 1. Exact for integers,
    # unlike np.log2 rounding at powers of two. 33..8191 -> 0..7.
    _, exponents = np.frexp(magnitude.astype(np.float64))
    exponent = (exponents - 1 - 5).astype(np.int32)
    mantissa = (magnitude >> (exponent + 1)) & 0x0F
    encoded = ~((sign << 7) | (exponent << 4) | mantissa) & 0xFF
    return encoded.astype(np.uint8).tobytes()


def ulaw_to_pcm16(ulaw: bytes) -> bytes:
    """G.711 μ-law decode, the exact inverse of the segment layout above."""
    encoded = ~np.frombuffer(ulaw, dtype=np.uint8).astype(np.int32) & 0xFF
    sign = encoded & 0x80
    exponent = (encoded >> 4) & 0x07
    mantissa = encoded & 0x0F
    magnitude = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    samples = np.where(sign != 0, -magnitude, magnitude)
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Rate-convert PCM16. The 16k -> 8k case (our outbound path) averages
    sample pairs first — a 2-tap low-pass with its zero at the new
    Nyquist, enough to keep the worst aliasing out of the voice band.
    Everything else is linear interpolation, which is fine for 8k -> 16k
    (nothing above 4 kHz exists to interpolate wrongly). A proper
    windowed-sinc filter was considered and rejected: telephone audio is
    band-limited to 3.4 kHz by the far end anyway."""
    if src_rate == dst_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    if samples.size == 0:
        return b""
    if src_rate == 2 * dst_rate:
        if samples.size % 2:
            samples = samples[:-1]
        out = (samples[0::2] + samples[1::2]) / 2.0
    else:
        n_out = int(round(samples.size * dst_rate / src_rate))
        positions = np.arange(n_out) * (samples.size / n_out)
        out = np.interp(positions, np.arange(samples.size), samples)
    return np.clip(np.rint(out), -32768, 32767).astype("<i2").tobytes()


def rms(pcm: bytes) -> float:
    """RMS of PCM16 in full-scale units (0..32767). Used for the inbound/
    outbound level report and the AEC observation during a live call."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples))))
