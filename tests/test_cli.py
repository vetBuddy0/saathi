"""saathi/cli.py -- the file that wires everything together, which
nothing covered until 2026-09-26. What's pinned: every tool `saathi run`
offers the model is registered, has a schema, and has its permission
granted; the intent handler runs a tool through that grant; the
controllers the tools close over are the same instances the screen
server is given; the session reads Chirp as the default backend from a
database with no preference; and `saathi voice` writes the row the panel
writes.

No Groq, no PulseAudio, no screen server: the pieces that touch the
outside are faked at the seams cli.py imports them from.
"""

from __future__ import annotations

import sys

import pytest

from saathi import cli
from saathi.identity.preferences import TTS_BACKEND_KEY, read_preference
from saathi.identity.store import IdentityStore


class FakeCascadeSession:
    instances: list["FakeCascadeSession"] = []

    def __init__(self, sink_id, **kwargs) -> None:
        self.sink_id = sink_id
        self.kwargs = kwargs
        self.intent = None
        FakeCascadeSession.instances.append(self)

    def on_intent(self, callback) -> None:
        self.intent = callback


def _local_audio_modules():
    """`saathi.audio.aec` and `.devices`, or None where they do not
    import: `pywebrtc-audio` and `pyudev` have no Android wheel, and on
    a machine with only the phone's packages the remote-mode tests
    below -- the ones that document that configuration -- must still
    run. cli.py imports both lazily, inside its local-mode branch, so
    remote mode never needs them; a local-mode test on such a machine
    fails inside `build_runtime()`, as `saathi run` would there."""
    try:
        from saathi.audio import aec, devices
    except ImportError:
        return None
    return aec, devices


@pytest.fixture
def seams(monkeypatch, tmp_path):
    """The outside world, faked where cli.py reaches for it."""
    from saathi.voice.engine import cascade

    monkeypatch.setenv("SAATHI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("SAATHI_AUDIO", raising=False)  # local mode, whatever the shell has
    found: dict[str, object] = {"input": True, "output": True}
    handles: dict[str, object] = {"value": None}

    local_audio = _local_audio_modules()
    if local_audio is not None:
        aec, devices = local_audio

        class FakeManager:
            def __init__(self, backend) -> None:
                pass

            def choose(self, direction):
                if not found[direction]:
                    return None
                return devices.Device(
                    id=f"{direction}-device", description="fake", direction=direction, bus="usb"
                )

        handles["value"] = aec.EchoCancelHandles(
            module_index="7", source_id="aec-src", sink_id="aec-sink"
        )
        monkeypatch.setattr(devices, "DeviceManager", FakeManager)
        monkeypatch.setattr(devices, "PulseAudioBackend", lambda: None)
        monkeypatch.setattr(aec, "ensure_echo_cancellation", lambda mic, speaker: handles["value"])
    monkeypatch.setattr(cascade, "CascadeSession", FakeCascadeSession)
    FakeCascadeSession.instances.clear()
    return {"found": found, "handles": handles, "data_dir": tmp_path}


@pytest.fixture
def wired(seams):
    return cli.build_runtime()


def test_every_tool_is_registered_with_a_schema_and_its_permission_granted(wired):
    names = {tool.name for tool in wired.registry}
    assert names == {"set_language", "correct_memory", "play_music", "call_contact"}
    for tool in wired.registry:
        assert tool.permission in cli.GRANTED_PERMISSIONS, tool.name
    assert {schema["function"]["name"] for schema in wired.tool_schemas} == names
    assert all(schema["function"]["description"] for schema in wired.tool_schemas)
    assert wired.session.kwargs["tool_schemas"] is wired.tool_schemas


def test_the_intent_handler_runs_a_tool_through_the_grant(wired):
    handle = wired.session.intent
    assert handle is wired.handle_intent
    # Reached the real media controller: nothing searched yet.
    assert handle("play_music", {"action": "play"})["status"] == "nothing_to_play"
    assert handle("no_such_tool", {}) == {"status": "error", "detail": "no such tool: no_such_tool"}
    # A permission not in the grant is refused before the handler runs.
    from saathi.tools.registry import Tool

    wired.registry.register(
        Tool(name="pay_bill", schema={}, permission="money", handler=lambda **_: 1)
    )
    assert handle("pay_bill", {})["status"] == "denied"


def test_the_controllers_are_built_once_and_the_tools_close_over_them(wired):
    assert wired.hold._cards is wired.cards
    assert wired.media._cards is wired.cards
    # The media tool the model calls is the same controller the screen is given.
    wired.media.last_results = []
    assert wired.handle_intent("play_music", {"action": "louder"})["volume"] == wired.media.volume


def test_the_session_is_built_on_the_echo_cancelled_sink_and_reads_the_store(wired, seams):
    session = wired.session
    assert session is FakeCascadeSession.instances[0]
    assert session.sink_id == "aec-sink"
    assert wired.capture_source_id == "aec-src"
    assert session.kwargs["identity_store"] is wired.store
    assert session.kwargs["language_preference"]() is None
    assert wired.store.path == seams["data_dir"] / "identity.sqlite3"
    # Every sentence goes through the /audio seam (audio/remote.py): the
    # phone when one is attached, the local sink otherwise.
    from saathi.audio.remote import RemoteAudio

    assert isinstance(wired.remote_audio, RemoteAudio)
    assert session.kwargs["player"] == wired.remote_audio.player


def test_the_media_controller_has_a_browser_target_only_while_a_phone_is_attached(wired):
    # The kiosk rule (tools/media.py: an embed refusal with nobody to
    # show the watch page is the browser's verdict too) rests on this
    # wiring -- the controller's own default assumes a taker -- so the
    # wiring is pinned here, not left to a lambda nobody tests.
    from types import SimpleNamespace

    assert wired.media._browser_taker() is False
    phone = SimpleNamespace(closed=False)
    wired.remote_audio.attach(phone, None)
    assert wired.media._browser_taker() is True
    wired.remote_audio.detach(phone)
    assert wired.media._browser_taker() is False


def test_a_database_with_no_preference_speaks_with_chirp(wired):
    assert wired.session.kwargs["backend_preference"]() == "google-chirp3-hd"


def test_a_stored_preference_wins_over_the_default(wired):
    from saathi.identity.preferences import write_preference

    write_preference(wired.store, TTS_BACKEND_KEY, "piper")
    assert wired.session.kwargs["backend_preference"]() == "piper"


def test_run_hands_the_screen_server_exactly_what_was_built(wired, monkeypatch):
    captured = {}

    def fake_run(core, host, port, **kwargs):
        captured.update(core=core, host=host, port=port, **kwargs)

    monkeypatch.setattr("saathi.screen.server.run", fake_run)
    monkeypatch.setattr(cli, "build_runtime", lambda: wired)
    assert cli.main(["run"]) == 0
    assert captured["core"] is wired.core
    assert captured["session"] is wired.session
    assert captured["capture_source_id"] == "aec-src"
    assert captured["store"] is wired.store
    assert captured["media"] is wired.media
    assert captured["cards"] is wired.cards
    assert captured["hold"] is wired.hold
    assert captured["remote_audio"] is wired.remote_audio
    assert (captured["host"], captured["port"]) == (
        wired.config.screen_host,
        wired.config.screen_port,
    )


def test_without_a_groq_key_the_screen_still_gets_the_controllers(seams, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    runtime = cli.build_runtime()
    assert runtime.session is None and runtime.capture_source_id is None
    assert runtime.registry is None
    assert runtime.cards is not None and runtime.hold is not None and runtime.media is not None
    assert runtime.remote_audio is not None  # the /audio route exists with or without an engine
    assert FakeCascadeSession.instances == []


def test_without_a_microphone_or_echo_cancel_the_device_runs_without_the_engine(seams):
    seams["found"]["input"] = False
    runtime = cli.build_runtime()
    assert runtime.session is None
    assert runtime.notes[0] == "No microphone/speaker found; running without the voice engine."
    assert runtime.registry is not None  # the tools exist; there is just no session to offer them

    seams["found"]["input"] = True
    seams["handles"]["value"] = None
    runtime = cli.build_runtime()
    assert runtime.session is None
    assert runtime.notes[0] == (
        "No system echo-cancel available; running without the voice engine."
    )


# -- saathi voice -----------------------------------------------------------


def _stored_backend(data_dir):
    with IdentityStore(data_dir / "identity.sqlite3") as store:
        return read_preference(store, TTS_BACKEND_KEY)


def test_voice_with_no_argument_shows_the_default_for_an_empty_database(seams, capsys):
    assert cli.main(["voice"]) == 0
    out = capsys.readouterr().out
    assert "tts_backend = google-chirp3-hd [default (no preference stored)]" in out
    assert _stored_backend(seams["data_dir"]) is None  # showing writes nothing


def test_voice_writes_the_same_row_the_panel_writes(seams, capsys):
    assert cli.main(["voice", "chirp"]) == 0
    assert _stored_backend(seams["data_dir"]) == "google-chirp3-hd"
    assert "effective on her next turn" in capsys.readouterr().out
    assert cli.main(["voice", "piper"]) == 0
    assert _stored_backend(seams["data_dir"]) == "piper"
    assert cli.main(["voice"]) == 0
    assert "tts_backend = piper [set]" in capsys.readouterr().out


def test_voice_refuses_an_unknown_backend(seams, capsys):
    assert cli.main(["voice", "bogus"]) == 2
    assert "unknown backend" in capsys.readouterr().out
    assert _stored_backend(seams["data_dir"]) is None


# -- calling (cloud/demo, 2026-09-26) ----------------------------------------

TWILIO_ENV = {
    "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
    "TWILIO_API_KEY": "SK" + "1" * 32,
    "TWILIO_API_SECRET": "s",
    "TWILIO_FROM_NUMBER": "+10000000000",
    "TWILIO_TEST_NUMBER": "+10000000001",
}


class FakeTunnel:
    """Stands in for CloudflaredQuickTunnel: `start()` blocks until
    released (the ~84 s the real hostname took), or raises."""

    release = None
    fail = None
    instances: list["FakeTunnel"] = []

    def __init__(self, local_port, startup_timeout=30.0):
        self.local_port = local_port
        self.startup_timeout = startup_timeout
        self.started = 0
        FakeTunnel.instances.append(self)

    @property
    def public_url(self):
        return "https://relay.example.test"

    def start(self):
        if FakeTunnel.release is not None:
            FakeTunnel.release.wait(2.0)
        if FakeTunnel.fail is not None:
            raise FakeTunnel.fail
        self.started += 1

    def stop(self):
        pass


class FakeMediaServer:
    def __init__(self, handler, ws_url, host="127.0.0.1", port=0):
        self.port = port

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def calling_seams(seams, monkeypatch):
    import threading

    from saathi.call import media, relay
    from saathi.call.twilio import FakeTwilioClient

    for name, value in TWILIO_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(relay, "find_cloudflared", lambda: "/usr/local/bin/cloudflared")
    monkeypatch.setattr(relay, "CloudflaredQuickTunnel", FakeTunnel)
    monkeypatch.setattr(media, "MediaServer", FakeMediaServer)
    client = FakeTwilioClient()
    client.call_resource = {"status": "ringing"}
    monkeypatch.setattr(cli, "_twilio_client", lambda creds: client)
    FakeTunnel.instances.clear()
    FakeTunnel.release = threading.Event()
    FakeTunnel.fail = None
    yield {"client": client, "release": FakeTunnel.release, "handles": seams["handles"]}
    FakeTunnel.release.set()


def _wait(condition, seconds=2.0):
    import time

    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_calling_registers_its_three_tools_and_grants_calls_and_contacts(calling_seams):
    runtime = cli.build_runtime()
    names = {tool.name for tool in runtime.registry}
    assert {"call_contact", "save_contact", "answer_card"} <= names
    for tool in runtime.registry:
        assert tool.permission in cli.GRANTED_PERMISSIONS, tool.name
    assert {"calls", "contacts"} <= cli.GRANTED_PERMISSIONS
    assert {s["function"]["name"] for s in runtime.tool_schemas} == names
    assert runtime.calling is not None
    assert runtime.calling.audio_ids == ("aec-src", "aec-sink")  # the echo-cancelled pair
    assert runtime.calling.controller._hold is runtime.hold  # the spacebar's hold seam


def test_the_relay_comes_up_at_boot_on_its_own_thread_not_at_the_first_dial(calling_seams):
    runtime = cli.build_runtime()  # returns at once: the tunnel is still resolving
    tunnel = FakeTunnel.instances[-1]
    assert tunnel.started == 0 and tunnel.startup_timeout == cli.RELAY_STARTUP_SECONDS
    assert not runtime.calling.ready.is_set()
    # A dial before it is up is an honest "starting up", not a wait or a crash.
    reply = runtime.handle_intent("call_contact", {"contact": "the test number"})
    assert reply["status"] == "unavailable" and "starting up" in reply["note"]
    assert calling_seams["client"].created == []
    calling_seams["release"].set()
    _wait(runtime.calling.ready.is_set)
    assert tunnel.started == 1
    reply = runtime.handle_intent("call_contact", {"contact": "the test number"})
    assert reply["status"] == "calling"
    assert calling_seams["client"].created[0][2] == "https://relay.example.test/twiml"
    assert runtime.hold.active  # the spacebar now means "hold to hang up"
    runtime.calling.controller.hangup(wait=True)
    assert not runtime.hold.active


def test_a_tunnel_that_fails_leaves_calling_unavailable_and_the_app_running(calling_seams):
    from saathi.call.relay import RelayError

    FakeTunnel.fail = RelayError("cloudflared printed no tunnel URL within 150s")
    calling_seams["release"].set()
    runtime = cli.build_runtime()
    _wait(lambda: runtime.calling.failed)
    reply = runtime.handle_intent("call_contact", {"contact": "Priya"})
    assert reply["status"] == "unavailable" and "no tunnel URL" in reply["note"]
    assert any("Calling off" in note for note in runtime.notes)
    assert runtime.session is not None  # the rest of the device is untouched


def test_without_twilio_variables_calling_is_unavailable_not_a_crash(seams, monkeypatch):
    for name in TWILIO_ENV:
        monkeypatch.delenv(name, raising=False)
    runtime = cli.build_runtime()
    assert runtime.calling is None
    assert runtime.session is not None
    reply = runtime.handle_intent("call_contact", {"contact": "Priya"})
    assert reply["status"] == "unavailable" and "TWILIO_ACCOUNT_SID" in reply["note"]
    assert any(note.startswith("Calling off: missing") for note in runtime.notes)
    assert "save_contact" not in {tool.name for tool in runtime.registry}


def test_without_cloudflared_calling_is_unavailable_not_a_crash(calling_seams, monkeypatch):
    from saathi.call import relay

    monkeypatch.setattr(relay, "find_cloudflared", lambda: None)
    runtime = cli.build_runtime()
    assert runtime.calling is None
    reply = runtime.handle_intent("call_contact", {"contact": "Priya"})
    assert reply["status"] == "unavailable" and "cloudflared" in reply["note"]
    assert FakeTunnel.instances == []


def test_without_an_echo_cancelled_pair_calling_is_unavailable(calling_seams):
    calling_seams["release"].set()
    calling_seams["handles"]["value"] = None
    runtime = cli.build_runtime()
    assert runtime.calling is None and runtime.session is None
    reply = runtime.handle_intent("call_contact", {"contact": "Priya"})
    assert reply["status"] == "unavailable" and "echo-cancel" in reply["note"]


# -- remote audio: the engine inside the phone (2026-10-08) -------------------


@pytest.fixture
def remote_seams(seams, monkeypatch):
    """`SAATHI_AUDIO=remote`, with every local probe turned into an
    assertion: nothing here may be constructed or called. Where the
    local audio modules do not import at all (the phone's packages,
    `_local_audio_modules`), a touch fails louder still, at the import."""

    def never(*args, **kwargs):
        raise AssertionError("remote mode must not touch local audio devices or echo-cancel")

    local_audio = _local_audio_modules()
    if local_audio is not None:
        aec, devices = local_audio
        monkeypatch.setattr(devices, "DeviceManager", never)
        monkeypatch.setattr(devices, "PulseAudioBackend", never)
        monkeypatch.setattr(aec, "ensure_echo_cancellation", never)
    monkeypatch.setenv("SAATHI_AUDIO", "remote")
    for name in TWILIO_ENV:
        monkeypatch.delenv(name, raising=False)
    return seams


def test_remote_mode_builds_no_device_manager_and_listens_only_through_remote_audio(
    remote_seams,
):
    runtime = cli.build_runtime()
    session = runtime.session
    assert session is FakeCascadeSession.instances[0]
    assert session.sink_id == cli.REMOTE_SINK_ID == "remote"
    assert session.kwargs["player"] == runtime.remote_audio.player
    assert runtime.capture_source_id is None  # the /audio client is the microphone
    assert any("SAATHI_AUDIO=remote" in note for note in runtime.notes)
    # Everything that is not audio is wired exactly as in local mode.
    assert session.kwargs["identity_store"] is runtime.store
    assert session.kwargs["tool_schemas"] is runtime.tool_schemas
    assert session.kwargs["backend_preference"]() == "google-chirp3-hd"
    assert session.kwargs["language_preference"]() is None
    assert session.intent is runtime.handle_intent
    names = {tool.name for tool in runtime.registry}
    assert {"set_language", "correct_memory", "play_music", "call_contact"} == names


def test_remote_mode_adds_the_phones_voice_to_the_sessions_backends(remote_seams):
    from saathi.voice.tts.remote_backend import RemoteTTSBackend

    runtime = cli.build_runtime()
    backends = runtime.session.kwargs["backends"]
    phone = backends["android-tts"]
    assert isinstance(phone, RemoteTTSBackend)
    assert phone._remote is runtime.remote_audio
    assert phone.available() == (False, "no phone is attached on /audio")
    # First in the dict: what cascade.py hands back when nothing is
    # available yet (boot, before the phone connects) -- see cli.py.
    assert next(iter(backends)) == "android-tts"
    # Linux with piper-tts installed: nothing is left out.
    assert "piper" in backends and "google-chirp3-hd" in backends


def test_remote_mode_leaves_piper_out_when_piper_tts_is_not_importable(
    remote_seams, monkeypatch
):
    monkeypatch.setitem(sys.modules, "piper", None)  # `import piper` now raises ImportError
    runtime = cli.build_runtime()
    backends = runtime.session.kwargs["backends"]
    assert "piper" not in backends
    assert "android-tts" in backends and "google-chirp3-hd" in backends


def test_remote_mode_reports_calling_unavailable_without_dialling(remote_seams):
    runtime = cli.build_runtime()
    assert runtime.calling is None
    reply = runtime.handle_intent("call_contact", {"contact": "Priya"})
    assert reply["status"] == "unavailable"
    assert any(note.startswith("Calling off") for note in runtime.notes)


def test_remote_mode_keeps_calling_unavailable_even_with_twilio_and_cloudflared(
    remote_seams, calling_seams, monkeypatch
):
    monkeypatch.setenv("SAATHI_AUDIO", "remote")  # calling_seams re-faked the rest
    calling_seams["release"].set()
    runtime = cli.build_runtime()
    assert runtime.calling is None
    assert FakeTunnel.instances == []  # no relay was even started
    reply = runtime.handle_intent("call_contact", {"contact": "Priya"})
    assert reply["status"] == "unavailable" and "echo-cancel" in reply["note"]
    assert "save_contact" not in {tool.name for tool in runtime.registry}


def test_remote_mode_drops_a_sentence_with_no_client_rather_than_paplay(
    remote_seams, tmp_path, caplog
):
    runtime = cli.build_runtime()
    wav_path = tmp_path / "sentence.wav"
    wav_path.write_bytes(b"RIFF")
    handle = runtime.remote_audio.player(cli.REMOTE_SINK_ID, wav_path)
    assert isinstance(handle, cli._DroppedPlayback) and handle.finished
    handle.wait()
    handle.stop()
    assert "dropping a sentence" in caplog.text


def test_remote_mode_hands_the_screen_server_no_capture_source(remote_seams, monkeypatch):
    captured = {}

    def fake_run(core, host, port, **kwargs):
        captured.update(core=core, host=host, port=port, **kwargs)

    monkeypatch.setattr("saathi.screen.server.run", fake_run)
    runtime = cli.build_runtime()
    monkeypatch.setattr(cli, "build_runtime", lambda: runtime)
    assert cli.main(["run"]) == 0
    assert captured["session"] is runtime.session
    assert captured["capture_source_id"] is None
    assert captured["remote_audio"] is runtime.remote_audio


def test_an_unknown_audio_mode_is_refused_not_guessed(seams, monkeypatch):
    monkeypatch.setenv("SAATHI_AUDIO", "phone")
    with pytest.raises(ValueError, match="SAATHI_AUDIO='phone'; expected one of local, remote"):
        cli.build_runtime()
