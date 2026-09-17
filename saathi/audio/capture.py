"""Mic frames, post-AEC: turns a PulseAudio source into a stream of
fixed-size chunks.

Exists as the seam between `audio/devices.py`/`audio/aec.py` (which
source) and whatever consumes audio (`audio/vad.py` now, `voice/session.py`
later): everything downstream gets exactly `chunk_bytes` bytes at a time,
16kHz mono 16-bit PCM, so it never needs to know or care that this is
PulseAudio underneath. Runs via `parec` (the raw streaming client), not
`parecord` (which writes a WAV file) — there is no file, only a live pipe.
"""

from __future__ import annotations

import subprocess
import threading
from typing import BinaryIO, Callable, Iterator

SAMPLE_RATE = 16000


def read_fixed_chunks(stream: BinaryIO, chunk_bytes: int) -> Iterator[bytes]:
    """Read `stream` in exactly `chunk_bytes`-sized pieces until it's
    exhausted. A trailing short read (EOF mid-chunk) is dropped rather than
    yielded — a partial chunk is not a valid input to `audio/vad.py`.
    Pulled out of `Capture` so the chunking logic is testable against a
    plain `BytesIO`, without a real `parec` process."""
    while True:
        chunk = stream.read(chunk_bytes)
        if not chunk:
            return
        if len(chunk) < chunk_bytes:
            return
        yield chunk


class Capture:
    """Reads fixed-size chunks from `source_id` on a background thread and
    calls `on_chunk(chunk)` for each one, until `stop()`."""

    def __init__(
        self, source_id: str, on_chunk: Callable[[bytes], None], chunk_bytes: int
    ) -> None:
        self._source_id = source_id
        self._on_chunk = on_chunk
        self._chunk_bytes = chunk_bytes
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [
                "parec",
                f"--device={self._source_id}",
                f"--rate={SAMPLE_RATE}",
                "--channels=1",
                "--format=s16le",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for chunk in read_fixed_chunks(self._proc.stdout, self._chunk_bytes):
            if not self._running:
                break
            self._on_chunk(chunk)

    def stop(self) -> None:
        self._running = False
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
