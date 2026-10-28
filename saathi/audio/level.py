"""Keeps the microphone out of clipping, because a clipped mic hears
nothing and nothing else in the device can tell.

Why (2026-10-28, "the stt doesnt work"): both transcribers answered a
known WAV correctly, yet live turns came back as nonsense. The laptop's
internal-mic source had been raised to 53% (ALSA Capture +30 dB plus
Internal Mic Boost +10 dB) -- by what is not known; the WirePlumber
state shows it changed minutes after boot. At that level the room's own
low rumble (90% of its energy under 300 Hz: fans, the desk) drove the
converter to full scale: 2-10% of samples at the rail in a silent room,
22% within 6 dB of it. Clipping is not recoverable downstream -- the
echo canceller's high-pass filter removes the rumble but not the
distortion it already caused, so the same recording, filtered, still
transcribed as "the main entrance of the Westin Suntec". At 20-30% the
room stayed far from the rail (peak 3,900-12,000 in silence) and the
filtered clip transcribed as the sentence played. The VAD and
endpointer saw the rail-to-rail rumble as endless speech too, so turns
did not end.

What this does: measures the *raw* hardware source (not the
echo-cancelled one -- the clipping happens before the canceller and is
smoothed by it) for a second, and if more than `TOO_HOT_FRACTION` of
samples are within 6 dB of the rail, lowers that source's volume a step
and measures again, down to `FLOOR_PERCENT`. At start-up and every few
minutes after, because whatever raised it may do so again.

Contested:
- Only ever lowers. Raising a quiet mic was the option that lost:
  something on this machine raises it already (a browser's
  autoGainControl is the usual suspect), and two controllers moving one
  volume in opposite directions oscillate. Too quiet degrades a
  transcript; clipping destroys it.
- Headroom is the test, not bare clipping. "Any sample at the rail" was
  tried first, live: it stepped 53% down to 43% and stopped, where 0.2%
  of a silent room still clipped -- her voice, louder than the room,
  would have clipped throughout. Keeping the room 6 dB under the rail
  leaves her voice that much.
- It changes a system setting, and the change outlives the process
  (WirePlumber saves it). That is the point: an old person's device
  has nobody to open a mixer.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from typing import Callable

from saathi.audio.capture import SAMPLE_RATE, parec_command

logger = logging.getLogger(__name__)

# Half of full scale: 6 dB under the rail (s16, max 32767).
HEADROOM_LEVEL = 16384
# Share of samples above HEADROOM_LEVEL, measured 2026-10-28 on the
# internal mic in a quiet room: 22% at 53%, 10.7% at 43%, 0.35% at 35%,
# 0.00% at 10-30% (0.03% at 30% with speech played at the mic).
TOO_HOT_FRACTION = 0.001
STEP_PERCENT = 10
# Below this the internal mic's speech fell under its noise floor
# (10%: speech peak ~800). A mic still too hot here is a hardware
# problem, said in the log, not chased further.
FLOOR_PERCENT = 15
# A source waking from suspend delivers a burst of garbage first
# (measured: rms 14,000 in the first 0.4 s at 10%).
SETTLE_SECONDS = 0.5
MEASURE_SECONDS = 1.0
CHECK_EVERY_SECONDS = 300.0

Recorder = Callable[[str, float], bytes]
PactlRunner = Callable[[list[str]], str]

_PERCENT_RE = re.compile(r"(\d+)%")


def hot_fraction(pcm: bytes) -> float:
    """Share of s16le samples in `pcm` within 6 dB of the rail."""
    count = len(pcm) // 2
    if count == 0:
        return 0.0
    samples = memoryview(pcm)[: count * 2].cast("h")
    hot = sum(1 for s in samples if s >= HEADROOM_LEVEL or s <= -HEADROOM_LEVEL)
    return hot / count


def parse_volume_percent(output: str) -> int | None:
    """The loudest channel's percentage from `pactl get-source-volume`,
    or None if it says none."""
    values = [int(v) for v in _PERCENT_RE.findall(output)]
    return max(values) if values else None


def _default_pactl(args: list[str]) -> str:
    result = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3.0)
    return result.stdout


def _parec_record(source_id: str, seconds: float) -> bytes:
    """`seconds` of 16 kHz mono s16le from `source_id`, after dropping
    the settling burst."""
    settle = int(SETTLE_SECONDS * SAMPLE_RATE) * 2
    wanted = int(seconds * SAMPLE_RATE) * 2
    proc = subprocess.Popen(
        parec_command(source_id), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    try:
        assert proc.stdout is not None
        data = b""
        while len(data) < settle + wanted:
            chunk = proc.stdout.read(settle + wanted - len(data))
            if not chunk:
                break
            data += chunk
        return data[settle:]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


class MicLevelGuard:
    """`MicLevelGuard(source_id).check()` lowers a hardware source that
    is too hot until it has headroom; `start()` repeats that on a thread."""

    def __init__(
        self,
        source_id: str,
        *,
        record: Recorder = _parec_record,
        pactl: PactlRunner = _default_pactl,
    ) -> None:
        self._source_id = source_id
        self._record = record
        self._pactl = pactl

    def _volume(self) -> int | None:
        return parse_volume_percent(self._pactl(["get-source-volume", self._source_id]))

    def _too_hot(self) -> bool:
        return hot_fraction(self._record(self._source_id, MEASURE_SECONDS)) > TOO_HOT_FRACTION

    def check(self) -> str | None:
        """Measure, and step down while too hot. Returns what was done,
        for the run's notes, or None when the level was already fine."""
        if not self._too_hot():
            return None
        start = volume = self._volume()
        if volume is None:
            return "Microphone is near clipping, and its volume couldn't be read to lower it."
        while volume > FLOOR_PERCENT:
            volume = max(FLOOR_PERCENT, volume - STEP_PERCENT)
            self._pactl(["set-source-volume", self._source_id, f"{volume}%"])
            if not self._too_hot():
                return f"Microphone was near clipping; lowered it from {start}% to {volume}%."
        return (
            f"Microphone still near clipping at {volume}% (lowered from {start}%); "
            "check the mic hardware."
        )

    def start(self, every: float = CHECK_EVERY_SECONDS) -> Callable[[], None]:
        """Check now and every `every` seconds on a daemon thread.
        Returns stop()."""
        stopped = threading.Event()

        def loop() -> None:
            while not stopped.is_set():
                try:
                    note = self.check()
                except Exception:  # parec/pactl missing or hung: never fatal
                    logger.exception("microphone level check failed")
                else:
                    if note:
                        logger.warning(note)
                        print(note)
                stopped.wait(every)

        threading.Thread(target=loop, name="saathi-mic-level", daemon=True).start()
        return stopped.set
