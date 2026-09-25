"""The `saathi` command.

`saathi run` starts `core.py` and the screen server so the spacebar can
drive the face. `saathi smoke` is `python -m saathi.smoke` under the same
entry point, for consistency — both are how a person on this machine
checks the device, not library code anything else here imports.
`saathi voice` reads or writes the one preference a person is most
likely to want from a shell (which backend she speaks with), against the
same database `saathi run` reads.

`run` is split so the wiring can be tested (2026-09-26): `build_runtime()`
constructs everything -- the store, the card/hold/media controllers, the
registry with every tool registered and every permission granted, the
session -- and returns it; `_run()` only hands that to the screen server.
Before the split nothing covered this file, and it is the file where a
tool registered but not granted, or a controller built twice, would be
invisible until someone spoke to the device.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

# Aliases a person would type; the ids are what the settings panel shows.
_VOICE_ALIASES = {"chirp": "google-chirp3-hd", "piper": "piper"}


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
    voice_parser = subparsers.add_parser(
        "voice", help="show or set which voice she speaks with (the tts_backend preference)"
    )
    voice_parser.add_argument(
        "backend",
        nargs="?",
        help="a backend id (google-chirp3-hd, piper, ...) or 'chirp'/'piper'; omit to show",
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

    if args.command == "voice":
        return _voice(args.backend)

    if args.command == "run":
        return _run()

    return 1


# The tools `saathi run` offers the model and the permission each one
# needs -- granted unconditionally, every one of them: set_language is
# device configuration, correct_memory is her own way to fix a wrong
# belief, play_music is a song on her own screen (DECISIONS 2026-09-25),
# "contacts" writes her memory and "calls" rings a phone (DECISIONS
# 2026-09-25, calls in scope; wired on cloud/demo 2026-09-26 with S1
# fixed and S2-S4 still open in TODO.md).
GRANTED_PERMISSIONS = frozenset({"preferences", "memory", "music", "calls", "contacts"})

# The quick tunnel's hostname took ~84 s to resolve on the first live
# run, so the relay comes up at boot on its own thread, not at the
# first dial; this is how long it may take before calling is reported
# as unavailable.
RELAY_STARTUP_SECONDS = 150.0


@dataclass
class Runtime:
    """Everything `saathi run` wires together, in one place a test can
    look at. `session` and `capture_source_id` are None when there is no
    `GROQ_API_KEY` or no usable microphone/speaker/echo-cancel -- the
    device then runs the checkpoint-1 fake press/release path."""

    config: Any
    core: Any
    store: Any
    cards: Any
    hold: Any
    media: Any
    registry: Any = None
    tool_schemas: list[dict] = field(default_factory=list)
    handle_intent: Callable[[str, dict], dict] | None = None
    session: Any = None
    capture_source_id: str | None = None
    notes: list[str] = field(default_factory=list)
    calling: "CallingRuntime | None" = None


@dataclass
class CallingRuntime:
    """Calling, when Twilio and cloudflared are both present. `ready` is
    set once the media server and the tunnel are up (a background thread
    at boot); until then, and if that fails, `call_contact` answers
    "unavailable" with `reason` instead of dialling -- the rest of the
    device never depends on it."""

    controller: Any
    ready: Any  # threading.Event
    reason: str = "Calling is still starting up."
    failed: bool = False
    audio_ids: tuple[str, str] | None = None  # (echo-cancelled source, sink)


def build_runtime() -> Runtime:
    from saathi.config import Config
    from saathi.core import Core
    from saathi.identity.store import IdentityStore
    from saathi.screen.cards import CardController, HoldController
    from saathi.tools.media import MediaController

    config = Config.load()
    core = Core()

    store = IdentityStore(config.identity_db_path)
    store.create()

    # Beside the face: the one card controller every tool asks through,
    # the hold seam (spacebar held to confirm), and the media panel.
    # Built once, before the session, so tools close over the same
    # instances the screen server installs its broadcast on -- two
    # controllers would mean a card a tool shows never reaches the
    # screen. Calling (PR #4) is parked; it will take `cards`/`hold`
    # from here rather than build its own.
    cards = CardController()
    hold = HoldController(cards)
    media = MediaController(cards=cards)
    runtime = Runtime(config=config, core=core, store=store, cards=cards, hold=hold, media=media)

    # The voice engine needs a speech-to-text + chat provider: OpenAI if
    # OPENAI_API_KEY is set (the demo choice), else Groq. See
    # voice/engine/provider.py.
    from saathi.voice.engine.provider import missing_key

    missing = missing_key()
    if missing is not None:
        runtime.notes.append(
            f"No AI provider ({missing} not set); running without the voice engine."
        )
        return runtime

    # One-hour spike wiring (2026-09-17): only actually talks to Groq
    # if a key is present, so checkpoint-1-only setups still get the
    # fake press/release path in screen/server.py unchanged.
    from saathi.audio.aec import EchoCancelHandles, ensure_echo_cancellation
    from saathi.audio.devices import DeviceManager, PulseAudioBackend
    from saathi.identity.correction import (
        CORRECT_MEMORY_DESCRIPTION,
        make_correct_memory_tool,
    )
    from saathi.identity.preferences import (
        LANGUAGE_KEY,
        TTS_BACKEND_KEY,
        threadsafe_reader,
    )
    from saathi.tools.language import SET_LANGUAGE_DESCRIPTION, make_set_language_tool
    from saathi.tools.llm_schema import tool_to_openai_schema
    from saathi.tools.media import MEDIA_DESCRIPTION, make_media_tool
    from saathi.tools.registry import PermissionDenied, Registry, UnknownTool
    from saathi.voice.engine.cascade import CascadeSession
    from saathi.voice.tts.registry import DEFAULT_PREFERRED_BACKEND_ID

    # Item G's spoken entry point. "The voice engine never executes
    # anything. It emits intent; the core validates; the tool
    # executes" (SPEC.md) -- this Registry and the permission grant
    # are that validation, living here rather than inside cascade.py.
    registry = Registry()
    set_language_tool = make_set_language_tool(store)
    correct_memory_tool = make_correct_memory_tool(store)
    media_tool = make_media_tool(media)
    for tool in (set_language_tool, correct_memory_tool, media_tool):
        registry.register(tool)

    def handle_intent(name: str, arguments: dict) -> dict:
        try:
            return registry.call(name, GRANTED_PERMISSIONS, **arguments)
        except UnknownTool:
            return {"status": "error", "detail": f"no such tool: {name}"}
        except PermissionDenied as exc:
            return {"status": "denied", "detail": str(exc)}

    tool_schemas = [
        tool_to_openai_schema(set_language_tool, SET_LANGUAGE_DESCRIPTION),
        tool_to_openai_schema(correct_memory_tool, CORRECT_MEMORY_DESCRIPTION),
        tool_to_openai_schema(media_tool, MEDIA_DESCRIPTION),
    ]
    runtime.registry = registry
    runtime.tool_schemas = tool_schemas
    runtime.handle_intent = handle_intent

    manager = DeviceManager(PulseAudioBackend())
    mic, speaker = manager.choose("input"), manager.choose("output")
    if mic is None or speaker is None:
        runtime.notes.append("No microphone/speaker found; running without the voice engine.")
        _build_calling(runtime, None)
        return runtime
    handles = ensure_echo_cancellation(mic.id, speaker.id)
    if not isinstance(handles, EchoCancelHandles):
        runtime.notes.append("No system echo-cancel available; running without the voice engine.")
        _build_calling(runtime, None)
        return runtime
    _build_calling(runtime, handles)
    # threadsafe_reader, not a lambda over `store`: these are called
    # from end_turn()'s executor thread and from the voice-warming
    # daemon thread, never from this one, and a sqlite3 connection
    # can't cross threads. Found live -- the first spacebar release of
    # a real run crashed the turn. See identity/preferences.py.
    #
    # No `tts_backend` row yet means Chirp (DEFAULT_PREFERRED_BACKEND_ID),
    # not the offline fallback: verified against the real database
    # path this time, not a scratch one (2026-09-26). `saathi voice`
    # switches an existing database.
    session = CascadeSession(
        handles.sink_id,
        backend_preference=threadsafe_reader(store, TTS_BACKEND_KEY, DEFAULT_PREFERRED_BACKEND_ID),
        language_preference=threadsafe_reader(store, LANGUAGE_KEY),
        identity_store=store,
        tool_schemas=tool_schemas,
    )
    session.on_intent(handle_intent)
    runtime.session = session
    runtime.capture_source_id = handles.source_id
    return runtime


def _unavailable_call_tool(reason: Callable[[], str]):
    """`call_contact` when calling can't dial: the same name and
    permission as the real tool, answering "unavailable" with a plain
    reason the model can say. Registered so "call Priya" gets an honest
    sentence, not "no such tool"."""
    from saathi.tools.registry import Tool

    def _call_contact(contact: Any = None, **_ignored: Any) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "note": f"{reason()} Say so plainly in one short sentence; don't offer to try.",
        }

    return Tool(
        name="call_contact",
        schema={"type": "object", "properties": {"contact": {"type": "string"}}},
        permission="calls",
        handler=_call_contact,
    )


def _build_calling(runtime: Runtime, handles: Any) -> None:
    """Calling, if Twilio is configured and cloudflared is installed
    (docs/completed/calling.md's cli diff). The echo-cancel ids come
    from aec.py here; nothing in saathi/call/ names a device. Missing
    credentials, a missing binary, no echo-cancel pair, or a tunnel that
    never resolves all end the same way: `call_contact` says calling is
    unavailable and the rest of the device carries on."""
    import threading

    from saathi.call.relay import find_cloudflared
    from saathi.call.twilio import TwilioCredentials
    from saathi.tools.calling import CALL_CONTACT_DESCRIPTION
    from saathi.tools.llm_schema import tool_to_openai_schema

    registry = runtime.registry
    if registry is None:
        return

    def off(reason: str) -> None:
        runtime.notes.append(f"Calling off: {reason}")
        tool = _unavailable_call_tool(lambda: f"Calling isn't available on this device: {reason}")
        registry.register(tool)
        runtime.tool_schemas.append(tool_to_openai_schema(tool, CALL_CONTACT_DESCRIPTION))

    creds = TwilioCredentials.from_env()
    if creds is None:
        off("missing " + ", ".join(TwilioCredentials.missing_names()) + ".")
        return
    if find_cloudflared() is None:
        off("cloudflared is not installed.")
        return
    if handles is None:
        off("no echo-cancelled microphone and speaker for the call audio.")
        return

    from saathi.call.audio import CallAudioBridge
    from saathi.call.choosing import ChoiceFlow
    from saathi.call.controller import CallController
    from saathi.call.media import DEFAULT_PORT, MediaServer
    from saathi.call.phone import infer_country
    from saathi.call.relay import CloudflaredQuickTunnel
    from saathi.call.saving import SaveFlow
    from saathi.identity.preferences import LANGUAGE_KEY, read_preference, write_preference
    from saathi.identity.store import IdentityStore
    from saathi.tools.calling import (
        ANSWER_CARD_DESCRIPTION,
        SAVE_CONTACT_DESCRIPTION,
        make_answer_card_tool,
        make_call_tool,
        make_contact_dialer,
        make_save_contact_tool,
    )
    from saathi.tools.registry import Tool

    path = runtime.store.path

    def locale():
        with IdentityStore(path) as own:  # handler threads: own connection
            return infer_country(
                read_preference(own, "country"), language=read_preference(own, LANGUAGE_KEY)
            )[0]

    def learn_country(iso):
        with IdentityStore(path) as own:
            write_preference(own, "country", iso)

    tunnel = CloudflaredQuickTunnel(DEFAULT_PORT, startup_timeout=RELAY_STARTUP_SECONDS)
    controller = CallController(
        creds,
        _twilio_client(creds),
        tunnel,
        _NoServer(),
        lambda: CallAudioBridge(handles.source_id, handles.sink_id),
        runtime.hold,
    )
    controller._server = MediaServer(controller, controller.ws_url, port=DEFAULT_PORT)
    calling = CallingRuntime(
        controller=controller,
        ready=threading.Event(),
        audio_ids=(handles.source_id, handles.sink_id),
    )
    runtime.calling = calling

    def bring_up() -> None:
        # The tunnel needs a minute or more to resolve; the face must
        # not wait for it. Until this sets `ready`, "call Priya" is
        # answered as "still starting up".
        try:
            controller.prepare()
        except Exception as exc:  # RelayError, OSError (port in use), ...
            calling.reason = f"Calling isn't available on this device: {exc}"
            calling.failed = True
            runtime.notes.append(f"Calling off: {exc}")
            return
        calling.reason = ""
        calling.ready.set()

    threading.Thread(target=bring_up, name="saathi-relay", daemon=True).start()

    saves = SaveFlow(path, runtime.cards, locale, on_country_confirmed=learn_country)
    choices = ChoiceFlow(runtime.cards, make_contact_dialer(controller))
    real_call = make_call_tool(controller, path, choices)

    def _gated_call(**arguments: Any) -> dict[str, Any]:
        if not calling.ready.is_set():
            return _unavailable_call_tool(lambda: calling.reason).handler(**arguments)
        return real_call.handler(**arguments)

    gated_call = Tool(
        name=real_call.name,
        schema=real_call.schema,
        permission=real_call.permission,
        handler=_gated_call,
    )
    tools = [
        (gated_call, CALL_CONTACT_DESCRIPTION),
        (make_save_contact_tool(saves), SAVE_CONTACT_DESCRIPTION),
        (make_answer_card_tool(runtime.cards, [saves, choices]), ANSWER_CARD_DESCRIPTION),
    ]
    for tool, description in tools:
        registry.register(tool)
        runtime.tool_schemas.append(tool_to_openai_schema(tool, description))


class _NoServer:
    """Placeholder until `MediaServer` (which needs the controller) is
    built -- the follow-up in docs/completed/calling.md is to give
    `CallController` a server factory instead."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def _twilio_client(creds):
    from saathi.call.twilio import RestTwilioClient

    return RestTwilioClient(creds)


def _run() -> int:
    from saathi.screen.server import run

    runtime = build_runtime()
    for note in runtime.notes:
        print(note)
    if runtime.calling is not None:
        print("Calling: starting the media server and the cloudflared tunnel in the background.")
    run(
        runtime.core,
        runtime.config.screen_host,
        runtime.config.screen_port,
        session=runtime.session,
        capture_source_id=runtime.capture_source_id,
        store=runtime.store,
        media=runtime.media,
        cards=runtime.cards,
        hold=runtime.hold,
    )
    if runtime.calling is not None:
        runtime.calling.controller.shutdown()
    return 0


def _voice(backend: str | None) -> int:
    """`saathi voice` shows the effective `tts_backend` preference of
    the configured database; `saathi voice <id>` writes it. The same
    row the Ctrl+L panel writes, effective on her next turn, no
    restart -- see identity/preferences.py."""
    from saathi.config import Config
    from saathi.identity.preferences import TTS_BACKEND_KEY, read_preference, write_preference
    from saathi.identity.store import IdentityStore
    from saathi.voice.tts.registry import DEFAULT_PREFERRED_BACKEND_ID, default_backends

    config = Config.load()
    backends = default_backends()
    with IdentityStore(config.identity_db_path) as store:
        store.create()
        if backend is None:
            stored = read_preference(store, TTS_BACKEND_KEY)
            effective = stored or DEFAULT_PREFERRED_BACKEND_ID
            origin = "set" if stored else "default (no preference stored)"
            available, reason = backends[effective].available() if effective in backends else (
                False,
                "unknown backend id",
            )
            print(f"{config.identity_db_path}: tts_backend = {effective} [{origin}]")
            state = "yes" if available else f"no -- {reason}"
            print(f"available now: {state}")
            return 0
        wanted = _VOICE_ALIASES.get(backend, backend)
        if wanted not in backends:
            print(f"unknown backend {backend!r}; one of: {', '.join(backends)}")
            return 2
        write_preference(store, TTS_BACKEND_KEY, wanted)
        available, reason = backends[wanted].available()
        print(f"{config.identity_db_path}: tts_backend = {wanted} (effective on her next turn)")
        if not available:
            print(f"note: {wanted} is not available on this machine right now -- {reason}; "
                  "Piper is used until it is")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
