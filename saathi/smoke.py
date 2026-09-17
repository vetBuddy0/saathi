"""`python -m saathi.smoke` — exercises real hardware and reports what is
plugged into this machine.

The device inventory (`main`) checks microphones and speakers via
`audio/devices.py`. `check_echo()` and `check_double_talk()` are the
hardware-in-the-loop AEC check SPEC.md's "Audio" section requires before
checkpoint 2 counts as done: a real speaker, a real mic, at listening
volume — the synthetic bench test in `tests/test_aec.py` proves the AEC
*algorithm* works, this proves the *device* does, on the actual machine it
runs on. Neither runs in CI; both need hardware CI doesn't have. They gate
a deploy the way `main`'s device check does, just less often — before
checkpoint 2 ships, not on every push.

Requires the `hardware` dependency group (`uv sync --group hardware`) for
`faster-whisper` — not a default install dependency, because nothing else
in this file needs it and it should not slow down the check that *does*
run everywhere.

Only the `SystemEchoCancel` path is exercised here, not `WebrtcAec` — this
machine has a working system echo-cancel, and building a real-time
frame-by-frame capture/process/playback loop for the in-process fallback
is real work with nothing to verify it against yet. Blocked, not stubbed:
if a future machine only has the `WebrtcAec` fallback, this check says so
and stops rather than pretending to have run it.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from saathi.audio.aec import EchoCancelHandles, ensure_echo_cancellation
from saathi.audio.devices import Device, DeviceManager, PulseAudioBackend

_TESTDATA_DIR = Path(__file__).parent / "audio" / "testdata"
_KNOWN_SENTENCE_WAV = _TESTDATA_DIR / "known_sentence.wav"
_KNOWN_SENTENCE_TEXT = "good morning today is tuesday and the weather is sunny"
_PLEASE_SPEAK_WAV = _TESTDATA_DIR / "please_speak.wav"

_SAMPLE_RATE = 16000
_ERLE_TARGET_DB = (25.0, 30.0)  # SPEC.md's target range


def _report(kind: str, devices: list[Device]) -> bool:
    """Print what was found for one direction. Returns whether it's ok."""
    if not devices:
        print(f"No {kind} found. Plug one in and try again.")
        return False
    print(f"{kind.capitalize()}s found ({len(devices)}):")
    for device in devices:
        print(f"  {device.description} [{device.bus or 'unknown bus'}]")
    return True


def main(argv: Sequence[str] | None = None, manager: DeviceManager | None = None) -> int:
    # `manager` is injectable so tests exercise the reporting/exit-code
    # logic against a FakeBackend, without a real PulseAudio server.
    manager = manager or DeviceManager(PulseAudioBackend())
    inputs_ok = _report("microphone", manager.enumerate("input"))
    outputs_ok = _report("speaker", manager.enumerate("output"))
    return 0 if inputs_ok and outputs_ok else 1


# -- hardware-in-the-loop AEC check ------------------------------------------


@dataclass
class AecHardwareResult:
    erle_db: float
    raw_rms: float
    cleaned_rms: float
    transcript: str
    passed: bool
    note: str = ""


def _read_wav_mono_float32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav_file:
        raw = wav_file.readframes(wav_file.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _rms(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x))))


def erle_db(
    raw: np.ndarray, cleaned: np.ndarray, sample_rate: int, skip_seconds: float = 0.5
) -> float:
    """Echo Return Loss Enhancement, in dB — SPEC.md's target is 25-30 dB.
    `raw` and `cleaned` should cover the same real-world time window (they
    won't be sample-aligned to better than process-start jitter, which is
    fine for a power ratio over whole clips, not fine for anything
    sample-exact). The first `skip_seconds` are dropped so AEC's own
    convergence time doesn't get counted against it."""
    skip = int(sample_rate * skip_seconds)
    raw_settled = raw[skip:]
    cleaned_settled = cleaned[skip : skip + len(raw_settled)]
    raw_power = _rms(raw_settled) ** 2
    cleaned_power = _rms(cleaned_settled) ** 2
    if cleaned_power <= 0:
        return float("inf")
    return 10.0 * float(np.log10(raw_power / cleaned_power))


def _sink_volume_pct(sink_id: str) -> int | None:
    output = subprocess.run(
        ["pactl", "list", "sinks"], capture_output=True, text=True, timeout=3.0
    ).stdout
    block_match = re.search(rf"Name: {re.escape(sink_id)}\n(?:.*\n)*?\tVolume:[^\n]*", output)
    if not block_match:
        return None
    percent_match = re.search(r"(\d+)%", block_match.group(0))
    return int(percent_match.group(1)) if percent_match else None


def _set_sink_volume_pct(sink_id: str, pct: int) -> None:
    subprocess.run(["pactl", "set-sink-volume", sink_id, f"{pct}%"], timeout=3.0)


def _play(sink_id: str, wav_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        ["paplay", f"--device={sink_id}", str(wav_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _record(source_id: str, out_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            "parecord",
            f"--device={source_id}",
            "--file-format=wav",
            f"--rate={_SAMPLE_RATE}",
            "--channels=1",
            "--format=s16le",
            str(out_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)


def _transcribe(wav_path: Path) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(wav_path), beam_size=5)
    return " ".join(segment.text for segment in segments).strip()


def _word_overlap_ratio(text: str, reference: str) -> float:
    words = set(re.findall(r"[a-z']+", text.lower()))
    reference_words = set(re.findall(r"[a-z']+", reference.lower()))
    if not words:
        return 0.0
    return len(words & reference_words) / len(words)


def _ensure_system_echo_cancel(mic: Device, speaker: Device) -> EchoCancelHandles | None:
    handles = ensure_echo_cancellation(mic.id, speaker.id)
    return handles if isinstance(handles, EchoCancelHandles) else None


def check_echo(listening_volume_pct: int = 40, tail_seconds: float = 1.0) -> AecHardwareResult:
    """Play the known sentence through the real speaker, capture through
    the real mic's AEC path, transcribe. A working AEC should leave
    nothing for STT to hear."""
    manager = DeviceManager(PulseAudioBackend())
    mic, speaker = manager.choose("input"), manager.choose("output")
    if mic is None or speaker is None:
        return AecHardwareResult(0, 0, 0, "", False, "no microphone/speaker on this machine")

    handles = _ensure_system_echo_cancel(mic, speaker)
    if handles is None:
        return AecHardwareResult(
            0, 0, 0, "", False, "no system echo-cancel available; WebrtcAec hardware path not built"
        )

    original_volume = _sink_volume_pct(handles.sink_id)
    _set_sink_volume_pct(handles.sink_id, listening_volume_pct)
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.wav"
            cleaned_path = Path(tmp_dir) / "cleaned.wav"

            raw_proc = _record(mic.id, raw_path)
            cleaned_proc = _record(handles.source_id, cleaned_path)
            time.sleep(0.5)  # let both captures actually open before playback
            _play(handles.sink_id, _KNOWN_SENTENCE_WAV).wait()
            time.sleep(tail_seconds)
            _stop(raw_proc)
            _stop(cleaned_proc)

            raw = _read_wav_mono_float32(raw_path)
            cleaned = _read_wav_mono_float32(cleaned_path)
            erle = erle_db(raw, cleaned, _SAMPLE_RATE)
            transcript = _transcribe(cleaned_path)
    finally:
        if original_volume is not None:
            _set_sink_volume_pct(handles.sink_id, original_volume)

    passed = transcript == ""
    return AecHardwareResult(
        erle_db=erle,
        raw_rms=_rms(raw),
        cleaned_rms=_rms(cleaned),
        transcript=transcript,
        passed=passed,
    )


def check_double_talk(
    listening_volume_pct: int = 40, speak_window_seconds: float = 6.0
) -> AecHardwareResult:
    """Play the known sentence while a person talks over it. A working AEC
    should remove the known sentence from the transcript while leaving the
    person's speech in it."""
    manager = DeviceManager(PulseAudioBackend())
    mic, speaker = manager.choose("input"), manager.choose("output")
    if mic is None or speaker is None:
        return AecHardwareResult(0, 0, 0, "", False, "no microphone/speaker on this machine")

    handles = _ensure_system_echo_cancel(mic, speaker)
    if handles is None:
        return AecHardwareResult(
            0, 0, 0, "", False, "no system echo-cancel available; WebrtcAec hardware path not built"
        )

    original_volume = _sink_volume_pct(handles.sink_id)
    _set_sink_volume_pct(handles.sink_id, listening_volume_pct)
    try:
        _play(handles.sink_id, _PLEASE_SPEAK_WAV).wait()

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.wav"
            cleaned_path = Path(tmp_dir) / "cleaned.wav"

            raw_proc = _record(mic.id, raw_path)
            cleaned_proc = _record(handles.source_id, cleaned_path)
            time.sleep(0.3)
            _play(handles.sink_id, _KNOWN_SENTENCE_WAV)  # don't wait: this is the overlap
            time.sleep(speak_window_seconds)
            _stop(raw_proc)
            _stop(cleaned_proc)

            raw = _read_wav_mono_float32(raw_path)
            cleaned = _read_wav_mono_float32(cleaned_path)
            erle = erle_db(raw, cleaned, _SAMPLE_RATE)
            transcript = _transcribe(cleaned_path)
    finally:
        if original_volume is not None:
            _set_sink_volume_pct(handles.sink_id, original_volume)

    overlap = _word_overlap_ratio(transcript, _KNOWN_SENTENCE_TEXT)
    # Heuristic, not a verdict: low overlap with the known sentence and a
    # non-empty transcript is *consistent with* the played sentence being
    # suppressed and live speech surviving. It cannot confirm what was
    # actually said — read the transcript yourself.
    passed = transcript != "" and overlap < 0.5
    note = f"word overlap with known sentence: {overlap:.0%} — read the transcript to confirm"
    return AecHardwareResult(
        erle_db=erle,
        raw_rms=_rms(raw),
        cleaned_rms=_rms(cleaned),
        transcript=transcript,
        passed=passed,
        note=note,
    )


def _print_result(label: str, result: AecHardwareResult) -> None:
    print(f"\n{label}")
    print(f"  ERLE: {result.erle_db:.1f} dB (target {_ERLE_TARGET_DB[0]}-{_ERLE_TARGET_DB[1]} dB)")
    print(f"  raw mic RMS: {result.raw_rms:.4f}   AEC-cleaned RMS: {result.cleaned_rms:.4f}")
    print(f"  transcript: {result.transcript!r}")
    if result.note:
        print(f"  note: {result.note}")
    print(f"  {'PASS' if result.passed else 'FAIL'}")


def cli(argv: Sequence[str] | None = None) -> int:
    """Entry point for both `python -m saathi.smoke` and `saathi smoke`.
    Plain `smoke` is the fast device-inventory check `main()` does;
    `--aec`/`--aec-double-talk` are the slower hardware-in-the-loop AEC
    checks, run explicitly rather than on every invocation."""
    args = list(argv if argv is not None else sys.argv[1:])
    if "--aec" in args:
        result = check_echo()
        _print_result("Echo-only check", result)
        return 0 if result.passed else 1
    if "--aec-double-talk" in args:
        result = check_double_talk()
        _print_result("Double-talk check", result)
        return 0 if result.passed else 1
    return main(args)


if __name__ == "__main__":
    raise SystemExit(cli())
