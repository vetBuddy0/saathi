"""Speaker output: plays a WAV file through a PulseAudio sink, and can be
stopped mid-sentence.

Exists as the mirror of `capture.py`, and as what feeds the reference
signal AEC needs (`aec.py`) — playback belongs on the sink `SystemEchoCancel`
returned, not the raw speaker, or nothing gets cancelled. `stop()` is what
barge-in calls: SPEC.md's ~300 ms budget is measured end-to-end, at the
speaker, in the barge-in test — killing the child process is necessary but
not sufficient, since PulseAudio's own buffer can hold audio that
outlives the process. Whether that buffer is short enough is exactly the
question the test answers; this file doesn't assume the answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class PlaybackHandle:
    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc

    @property
    def finished(self) -> bool:
        return self._proc.poll() is not None

    def wait(self) -> None:
        self._proc.wait()

    def stop(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()


def play(sink_id: str, wav_path: Path) -> PlaybackHandle:
    proc = subprocess.Popen(
        ["paplay", f"--device={sink_id}", str(wav_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return PlaybackHandle(proc)
