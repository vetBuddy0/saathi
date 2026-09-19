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
    from saathi.identity.store import IdentityStore
    from saathi.screen.server import run

    config = Config.load()
    core = Core()

    store = IdentityStore(config.identity_db_path)
    store.create()

    session = None
    capture_source_id = None
    if os.environ.get("GROQ_API_KEY"):
        # One-hour spike wiring (2026-09-17): only actually talks to Groq
        # if a key is present, so checkpoint-1-only setups still get the
        # fake press/release path in screen/server.py unchanged.
        from saathi.audio.aec import EchoCancelHandles, ensure_echo_cancellation
        from saathi.audio.devices import DeviceManager, PulseAudioBackend
        from saathi.identity.preferences import (
            LANGUAGE_KEY,
            TTS_BACKEND_KEY,
            threadsafe_reader,
        )
        from saathi.tools.language import SET_LANGUAGE_DESCRIPTION, make_set_language_tool
        from saathi.tools.llm_schema import tool_to_openai_schema
        from saathi.tools.registry import PermissionDenied, Registry, UnknownTool
        from saathi.voice.engine.cascade import CascadeSession
        from saathi.voice.tts.registry import DEFAULT_BACKEND_ID

        # Item G's spoken entry point. "The voice engine never executes
        # anything. It emits intent; the core validates; the tool
        # executes" (SPEC.md) -- this Registry and the permission grant
        # below are that validation, living here rather than inside
        # cascade.py. set_language needs no elevated consent beyond
        # what any device-configuration change would (unlike calls/
        # music, explicitly out of v1's scope): granted unconditionally.
        registry = Registry()
        set_language_tool = make_set_language_tool(store)
        registry.register(set_language_tool)
        granted_permissions = frozenset({"preferences"})

        def handle_intent(name: str, arguments: dict) -> dict:
            try:
                return registry.call(name, granted_permissions, **arguments)
            except UnknownTool:
                return {"status": "error", "detail": f"no such tool: {name}"}
            except PermissionDenied as exc:
                return {"status": "denied", "detail": str(exc)}

        tool_schemas = [tool_to_openai_schema(set_language_tool, SET_LANGUAGE_DESCRIPTION)]

        manager = DeviceManager(PulseAudioBackend())
        mic, speaker = manager.choose("input"), manager.choose("output")
        if mic is not None and speaker is not None:
            handles = ensure_echo_cancellation(mic.id, speaker.id)
            if isinstance(handles, EchoCancelHandles):
                # threadsafe_reader, not a lambda over `store`: these are
                # called from end_turn()'s executor thread and from the
                # voice-warming daemon thread, never from this one, and
                # a sqlite3 connection can't cross threads. Found live --
                # the first spacebar release of a real run crashed the
                # turn. See identity/preferences.py.
                session = CascadeSession(
                    handles.sink_id,
                    backend_preference=threadsafe_reader(
                        store, TTS_BACKEND_KEY, DEFAULT_BACKEND_ID
                    ),
                    language_preference=threadsafe_reader(store, LANGUAGE_KEY),
                    identity_store=store,
                    tool_schemas=tool_schemas,
                )
                session.on_intent(handle_intent)
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
        store=store,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
