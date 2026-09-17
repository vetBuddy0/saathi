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
    # `argv=None` is passed straight to `parser.parse_args` below, which
    # then reads `sys.argv[1:]` itself — that's what makes the installed
    # `saathi` console script (which calls `main()` with no arguments)
    # work; normalizing `None` to `[]` here instead broke exactly that.
    parser = argparse.ArgumentParser(prog="saathi")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="start core.py and the screen server")
    smoke_parser = subparsers.add_parser("smoke", help="report what hardware is plugged in")
    smoke_parser.add_argument(
        "--aec", action="store_true", help="hardware-in-the-loop AEC check (echo only)"
    )
    smoke_parser.add_argument(
        "--aec-double-talk",
        action="store_true",
        help="hardware-in-the-loop AEC check (double-talk)",
    )
    smoke_parser.add_argument(
        "--barge-in",
        action="store_true",
        help="hardware-in-the-loop barge-in check (stop latency + post-interrupt bleed)",
    )
    args = parser.parse_args(argv)

    if args.command == "smoke":
        from saathi.smoke import cli as smoke_cli

        smoke_args = []
        if args.aec:
            smoke_args.append("--aec")
        if args.aec_double_talk:
            smoke_args.append("--aec-double-talk")
        if args.barge_in:
            smoke_args.append("--barge-in")
        return smoke_cli(smoke_args)

    if args.command == "run":
        return _run()

    return 1


def _run() -> int:
    import os

    from saathi.config import Config
    from saathi.core import Core
    from saathi.screen.server import run

    config = Config.load()
    core = Core()

    session = None
    capture_source_id = None
    if os.environ.get("GROQ_API_KEY"):
        # One-hour spike wiring (2026-09-17): only actually talks to Groq
        # if a key is present, so checkpoint-1-only setups still get the
        # fake press/release path in screen/server.py unchanged.
        from saathi.audio.aec import EchoCancelHandles, ensure_echo_cancellation
        from saathi.audio.devices import DeviceManager, PulseAudioBackend
        from saathi.voice.engine.cascade import CascadeSession

        manager = DeviceManager(PulseAudioBackend())
        mic, speaker = manager.choose("input"), manager.choose("output")
        if mic is not None and speaker is not None:
            handles = ensure_echo_cancellation(mic.id, speaker.id)
            if isinstance(handles, EchoCancelHandles):
                session = CascadeSession(handles.sink_id)
                capture_source_id = handles.source_id
            else:
                print("No system echo-cancel available; running without the voice engine.")
        else:
            print("No microphone/speaker found; running without the voice engine.")

    run(
        core,
        config.screen_host,
        config.screen_port,
        session=session,
        capture_source_id=capture_source_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
