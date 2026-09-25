"""The audio bridge: echo-cancelled mic -> 20 ms μ-law frames outbound;
inbound μ-law -> the echo-cancelled sink, streamed.

Exists because `audio/playback.py` is `paplay` on a *file* — there is no
streaming playback anywhere in this codebase, and a phone call is the
first thing that needs one. `PcmSinkWriter` is that: a `pacat --playback`
process fed on stdin. It lives here rather than in `audio/playback.py`
because that file is another stream's territory; a general
`play_stream()` there is the right home and is asked for in
`docs/completed/calling.md`.

Why the *echo-cancelled* source and sink, same as the cascade: the
far-end voice comes out of the speaker, the mic hears it, and without
AEC it would go straight back down the line — the person on the phone
hears themselves, and she is heard twice. Playing the far end through
the ec sink is what gives the canceller its reference signal; capturing
from the ec source is what removes it. `Capture` (`audio/capture.py`) is
reused as-is: 16 kHz mono PCM16 in fixed chunks, on its own thread.

Frame size: Twilio wants 20 ms per message. 20 ms at 16 kHz is 640
bytes; downsampled to 8 kHz that is 160 samples, 160 μ-law bytes — one
`Capture` chunk becomes exactly one Twilio frame, and the capture clock
paces the stream, so no timer is needed.

`inject()` exists for the live dial check: it plays a known sentence
*into the call* (replacing the mic for its duration) so the far end can
be measured hearing something predictable. It is not a product feature.
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

from saathi.audio.capture import Capture
from saathi.call.codec import (
    DEVICE_RATE,
    TELEPHONE_RATE,
    pcm16_to_ulaw,
    resample_pcm16,
    rms,
    ulaw_to_pcm16,
)

FRAME_MS = 20
OUTBOUND_CHUNK_BYTES = DEVICE_RATE * 2 * FRAME_MS // 1000  # 640: 20 ms of 16 kHz PCM16
ULAW_FRAME_BYTES = TELEPHONE_RATE * FRAME_MS // 1000  # 160

Popen = Callable[[list[str]], subprocess.Popen]


def _default_popen(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class PcmSinkWriter:
    """Streams raw PCM16 mono into a PulseAudio sink via `pacat`'s stdin.
    `popen` is injectable so a test can run a real stand-in process
    (`cat` into a file) instead of pacat — a real pipe to a real child,
    not a mock of `subprocess`."""

    def __init__(
        self, sink_id: str, sample_rate: int = TELEPHONE_RATE, popen: Popen = _default_popen
    ) -> None:
        self._args = [
            "pacat",
            "--playback",
            f"--device={sink_id}",
            f"--rate={sample_rate}",
            "--channels=1",
            "--format=s16le",
            # Bound Pulse's own buffering: a phone call needs the far end
            # heard now, not 200 ms from now, and barge-in-style stops
            # must not have a long tail queued behind them.
            "--latency-msec=60",
        ]
        self._popen = popen
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.bytes_written = 0

    @property
    def command(self) -> list[str]:
        return list(self._args)

    def open(self) -> None:
        self._proc = self._popen(self._args)

    def write(self, pcm: bytes) -> bool:
        """Returns False once the child is gone (pipe broken or never
        opened) so the caller can stop feeding it; never raises."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.stdin is None or proc.poll() is not None:
                return False
            try:
                proc.stdin.write(pcm)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return False
            self.bytes_written += len(pcm)
            return True

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)


class CallAudio(Protocol):
    """What `controller.py` drives per call. `start(send)`: begin sending
    outbound μ-law frames through `send` (called from any thread).
    `feed_inbound`: one μ-law frame from the far end. `stop`: release
    the mic and the sink."""

    def start(self, send: Callable[[bytes], None]) -> None: ...

    def feed_inbound(self, ulaw: bytes) -> None: ...

    def stop(self) -> None: ...


@dataclass
class BridgeStats:
    frames_out: int = 0
    frames_in: int = 0
    _sq_out: float = field(default=0.0, repr=False)
    _sq_in: float = field(default=0.0, repr=False)

    def note_out(self, pcm: bytes) -> None:
        self.frames_out += 1
        self._sq_out += rms(pcm) ** 2

    def note_in(self, pcm: bytes) -> None:
        self.frames_in += 1
        self._sq_in += rms(pcm) ** 2

    @property
    def rms_out(self) -> float:
        return (self._sq_out / self.frames_out) ** 0.5 if self.frames_out else 0.0

    @property
    def rms_in(self) -> float:
        return (self._sq_in / self.frames_in) ** 0.5 if self.frames_in else 0.0


class CallAudioBridge:
    """The real `CallAudio`: `Capture` on the ec source, `PcmSinkWriter`
    on the ec sink. Both factories are injectable; the ids are whatever
    `audio/aec.py`'s `ensure_echo_cancellation()` returned — never a
    name written down anywhere."""

    def __init__(
        self,
        source_id: str,
        sink_id: str,
        capture_factory: Callable[[str, Callable[[bytes], None], int], Capture] = Capture,
        writer_factory: Callable[[str], PcmSinkWriter] = PcmSinkWriter,
    ) -> None:
        self._source_id = source_id
        self._sink_id = sink_id
        self._capture_factory = capture_factory
        self._writer_factory = writer_factory
        self._capture: Capture | None = None
        self._writer: PcmSinkWriter | None = None
        self._send: Callable[[bytes], None] | None = None
        self._injected: deque[bytes] = deque()
        self._lock = threading.Lock()
        self.stats = BridgeStats()
        self.inbound_tap: Callable[[bytes], None] | None = None  # PCM16 8 kHz, for measurement
        self.outbound_tap: Callable[[bytes], None] | None = None  # PCM16 16 kHz, for measurement

    def start(self, send: Callable[[bytes], None]) -> None:
        self._send = send
        self._writer = self._writer_factory(self._sink_id)
        self._writer.open()
        self._capture = self._capture_factory(self._source_id, self._on_chunk, OUTBOUND_CHUNK_BYTES)
        self._capture.start()

    def _on_chunk(self, pcm16k: bytes) -> None:
        send = self._send
        if send is None:
            return
        if self.outbound_tap is not None:
            self.outbound_tap(pcm16k)
        with self._lock:
            injected = self._injected.popleft() if self._injected else None
        source = injected if injected is not None else pcm16k
        pcm8k = resample_pcm16(source, DEVICE_RATE, TELEPHONE_RATE)
        self.stats.note_out(pcm8k)
        send(pcm16_to_ulaw(pcm8k))

    def feed_inbound(self, ulaw: bytes) -> None:
        pcm8k = ulaw_to_pcm16(ulaw)
        self.stats.note_in(pcm8k)
        if self.inbound_tap is not None:
            self.inbound_tap(pcm8k)
        if self._writer is not None:
            self._writer.write(pcm8k)

    def inject(self, pcm16k: bytes) -> None:
        """Queue 16 kHz PCM16 to be sent in place of the mic, one 20 ms
        chunk per capture tick. See module docstring: a measurement aid."""
        with self._lock:
            for offset in range(0, len(pcm16k) - OUTBOUND_CHUNK_BYTES + 1, OUTBOUND_CHUNK_BYTES):
                self._injected.append(pcm16k[offset : offset + OUTBOUND_CHUNK_BYTES])

    def stop(self) -> None:
        self._send = None
        capture, self._capture = self._capture, None
        writer, self._writer = self._writer, None
        if capture is not None:
            capture.stop()
        if writer is not None:
            writer.close()
