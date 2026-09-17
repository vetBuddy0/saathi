"""Runtime configuration.

One small dataclass instead of a settings framework: checkpoint 1 has three
knobs (where the identity DB lives, and what host/port the screen server
binds). Env vars override defaults so the same code runs on a dev laptop and
the kiosk machine without editing source — the alternative, a config file
format, was rejected as premature for three values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
        )
