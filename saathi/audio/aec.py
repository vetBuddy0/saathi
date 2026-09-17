"""Local acoustic echo cancellation — mandatory, and the first thing
checkpoint 2 builds because "everything else depends on it, including the
open mic after Saathi speaks" (this checkpoint's brief). Without it the
device transcribes its own voice, and there is no way to let the mic stay
open once she starts talking back.

Two layers, tried in that order, matching SPEC.md's "try PipeWire
module-echo-cancel first ... fall back to pywebrtc-audio":

`SystemEchoCancel` asks PulseAudio's own `module-echo-cancel` to do the
work *below* this process — it owns the reference signal at the point
where audio actually leaves for the speaker, which is the part SPEC.md
calls "actually hard" about doing AEC correctly. `WebrtcAec` is the
in-process fallback for a machine where that module isn't loaded or
loadable, running the same webrtc audio-processing pipeline Chrome does.

Contested: this dev machine runs PulseAudio, not PipeWire (checkpoint 1's
note still applies). PulseAudio ships the identical `module-echo-cancel`
that PipeWire wraps, so "try [it] first" is satisfied by asking whichever
server is actually running for it, never by requiring PipeWire
specifically. On this box the module is already loaded (id 28, for the
built-in card) — `SystemEchoCancel.find()` is what notices that instead of
loading a second, redundant one.

Contested: the bench test in `tests/test_aec.py` feeds `WebrtcAec`
synthetic near/far signals rather than playing a tone out of a real
speaker and recording it with a real mic. SPEC.md's Tests section
requires the whole suite to run headless, and CI has no speaker or mic to
record from. A synthetic near/far pair exercises the exact same DSP path a
live tone would — it is real AEC given known input, not a mock of AEC —
and a threshold on its residual is a meaningful regression test. A
hardware-in-the-loop version of the same check belongs in `smoke.py` once
there is a real speaker/mic pair to run it against; that is not this file.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable

import numpy as np
from pywebrtc_audio import AudioProcessor

_BLOCK_RE = re.compile(r"^(Source|Sink) #(\d+)\n((?:.*\n?)*?)(?=\n\S|\Z)", re.MULTILINE)
_FIELD_RE = re.compile(r"^\t(Name|Owner Module): (.*)$", re.MULTILINE)
_PROP_RE = re.compile(r'^\t{1,2}([\w.]+) = "(.*)"$', re.MULTILINE)

PactlRunner = Callable[[list[str]], str]


def _default_pactl(args: list[str]) -> str:
    result = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3.0)
    return result.stdout


def _filter_devices(output: str) -> list[dict]:
    """Parse `pactl list sources|sinks` text for entries PulseAudio itself
    tags `device.class = "filter"` — the echo-cancel module's own virtual
    devices, never real hardware (see `audio/devices.py`, which excludes
    exactly these for the opposite reason)."""
    devices = []
    for match in _BLOCK_RE.finditer(output + "\n\n"):
        block = match.group(3)
        fields = dict(_FIELD_RE.findall(block))
        props = dict(_PROP_RE.findall(block))
        if props.get("device.class") != "filter":
            continue
        name = fields.get("Name")
        if not name:
            continue
        devices.append(
            {
                "name": name,
                "module": fields.get("Owner Module"),
                "master": props.get("device.master_device"),
            }
        )
    return devices


@dataclass(frozen=True)
class EchoCancelHandles:
    """The AEC-processed source and sink `capture.py`/`playback.py` should
    use instead of the raw mic/speaker — capturing from `source_id` gives
    audio with the device's own output already cancelled out; playing
    through `sink_id` is what makes that cancellation possible."""

    module_index: str
    source_id: str
    sink_id: str


class SystemEchoCancel:
    """Finds or loads PulseAudio's `module-echo-cancel` for a given
    mic/speaker pair — the "try this first" path. `run` is injectable so
    tests exercise the parsing and command construction without a real
    PulseAudio server (mirrors `audio/devices.py`'s `PulseAudioBackend`)."""

    def __init__(self, run: PactlRunner = _default_pactl) -> None:
        self._run = run

    def find(self, mic_id: str, speaker_id: str) -> EchoCancelHandles | None:
        """Returns the existing pair for this mic/speaker, if PulseAudio
        already has one loaded — never loads anything itself.

        Verified on the dev machine: a `module-echo-cancel` loaded *without*
        explicit `source_master`/`sink_master` (e.g. by distro config, not
        by us) reports its source's `device.master_device` as the default
        *sink*, not the mic — so it correctly does not match here and a
        properly-scoped pair gets loaded instead. That is intentional, not
        a gap to close: matching an unscoped module by guesswork would be
        exactly the kind of assumption SPEC.md's "no device name" rule
        exists to rule out."""
        sources = _filter_devices(self._run(["list", "sources"]))
        sinks = _filter_devices(self._run(["list", "sinks"]))
        source = next((s for s in sources if s["master"] == mic_id), None)
        sink = next((s for s in sinks if s["master"] == speaker_id), None)
        if source is None or sink is None:
            return None
        return EchoCancelHandles(
            module_index=source["module"], source_id=source["name"], sink_id=sink["name"]
        )

    def load(self, mic_id: str, speaker_id: str) -> EchoCancelHandles:
        """Loads a new `module-echo-cancel` for this pair. Re-queries
        PulseAudio afterwards for the resulting source/sink names rather
        than assuming its naming convention — that is PulseAudio's
        implementation detail, not something to hardcode here."""
        self._run(
            [
                "load-module",
                "module-echo-cancel",
                f"source_master={mic_id}",
                f"sink_master={speaker_id}",
                "aec_method=webrtc",
            ]
        )
        handles = self.find(mic_id, speaker_id)
        if handles is None:
            raise RuntimeError(
                "module-echo-cancel was loaded but its source/sink pair wasn't found"
            )
        return handles

    def ensure(self, mic_id: str, speaker_id: str) -> EchoCancelHandles:
        return self.find(mic_id, speaker_id) or self.load(mic_id, speaker_id)

    def unload(self, handles: EchoCancelHandles) -> None:
        self._run(["unload-module", handles.module_index])


class WebrtcAec:
    """Fallback AEC, run in this process: the webrtc audio-processing
    pipeline (echo cancellation + noise suppression), used only when
    `SystemEchoCancel` can't give us a system-level pair."""

    def __init__(self, sample_rate: int = 16000, stream_delay_ms: int = 40) -> None:
        self._processor = AudioProcessor(
            sample_rate=sample_rate,
            echo_cancellation=True,
            noise_suppression=True,
            auto_gain_control=False,
            stream_delay_ms=stream_delay_ms,
        )

    def process(self, near: np.ndarray, far: np.ndarray) -> np.ndarray:
        """`near` is what the mic picked up (speech + echo + noise);
        `far` is the reference signal that was sent to the speaker.
        Returns `near` with the echo it shares with `far` removed."""
        return self._processor.process(near, far)


def ensure_echo_cancellation(
    mic_id: str, speaker_id: str, system: SystemEchoCancel | None = None
) -> EchoCancelHandles | WebrtcAec:
    """SPEC.md: try the system module first, fall back to `WebrtcAec`.
    A system pair means "capture from `.source_id`, play through
    `.sink_id`"; a `WebrtcAec` means "call `.process(near, far)` yourself"
    — `capture.py`/`playback.py` are what will act on either, once built."""
    system = system or SystemEchoCancel()
    try:
        return system.ensure(mic_id, speaker_id)
    except (subprocess.SubprocessError, FileNotFoundError, RuntimeError):
        return WebrtcAec()
