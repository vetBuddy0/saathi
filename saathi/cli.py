"""The `saathi` command.

`saathi run` starts `core.py` and the screen server so the spacebar can
drive the face. `saathi smoke` is `python -m saathi.smoke` under the same
entry point, for consistency — both are how a person on this machine
checks the device, not library code anything else here imports.
"""

from __future__ import annotations

import argparse
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saathi")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="start core.py and the screen server")
    subparsers.add_parser("smoke", help="report what hardware is plugged in")
    args = parser.parse_args(argv)

    if args.command == "smoke":
        from saathi.smoke import main as smoke_main

        return smoke_main([])

    if args.command == "run":
        return _run()

    return 1


def _run() -> int:
    from saathi.config import Config
    from saathi.core import Core
    from saathi.screen.server import run

    config = Config.load()
    core = Core()
    run(core, config.screen_host, config.screen_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
