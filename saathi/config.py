"""Runtime configuration.

One small dataclass instead of a settings framework: checkpoint 1 has three
knobs (where the identity DB lives, and what host/port the screen server
binds). Env vars override defaults so the same code runs on a dev laptop and
the kiosk machine without editing source — the alternative, a config file
format, was rejected as premature for three values.

A fourth knob since 2026-10-08, `SAATHI_AUDIO`: "local" (the default --
PulseAudio devices, echo-cancel, `parec`/`paplay`) or "remote" (the
engine inside the Android app: the `/audio` client is the only
microphone and speaker, nothing local is probed). It lives here, not in
`cli.py`, because it is deployment configuration of the same kind as
the port -- set once for the machine the engine runs on. A value that
is neither is refused at load, not guessed: the person who typed it is
at the shell, and the phone build sets it by construction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# SAATHI_AUDIO: where the microphone and the speaker are.
AUDIO_MODES = ("local", "remote")


def _audio_mode() -> str:
    mode = os.environ.get("SAATHI_AUDIO", "local").strip().lower() or "local"
    if mode not in AUDIO_MODES:
        raise ValueError(f"SAATHI_AUDIO={mode!r}; expected one of {', '.join(AUDIO_MODES)}")
    return mode


def _data_dir() -> Path:
    path = Path(os.environ.get("SAATHI_DATA_DIR", Path.home() / ".saathi"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    identity_db_path: Path
    screen_host: str
    screen_port: int
    audio_mode: str = "local"

    @classmethod
    def load(cls) -> "Config":
        data_dir = _data_dir()
        return cls(
            data_dir=data_dir,
            identity_db_path=Path(
                os.environ.get("SAATHI_IDENTITY_DB", data_dir / "identity.sqlite3")
            ),
            screen_host=os.environ.get("SAATHI_SCREEN_HOST", "127.0.0.1"),
            screen_port=int(os.environ.get("SAATHI_SCREEN_PORT", "8765")),
            audio_mode=_audio_mode(),
        )
