"""saathi/android.py -- the entry point Chaquopy calls. What's pinned:
`start(config)` puts the engine on the given host and port with the
face page on `/` and state+settings on `/ws`, sets the environment the
engine reads (`SAATHI_DATA_DIR`, `SAATHI_AUDIO=remote`,
`SAATHI_AI_CLIENT=rest`, the keys it was handed and no others), writes
the Google credential to `<data_dir>/gcp.json` with mode 0600, and
returns only once `GET /` answers; `stop()` closes the port, stops
calling if there was any, closes the store and restores the
environment; a second start after a stop works, on the very same port;
a start that cannot finish leaves nothing running; and importing the
module does nothing at all.

The session is `test_cli.py`'s fake, for the reason that file gives and
one more: the real `CascadeSession` warms a voice at construction, and
on a Linux box with `piper-tts` installed that means a voice model
downloaded into `~/.saathi` from inside a test. What the real session
does in remote mode is `test_cli.py`'s and `test_tts_remote.py`'s to
prove; what this file proves is the wiring from the shell's dict to a
server a WebView can load. The one start without any AI key runs the
real `build_runtime()` end to end, since no session is built there.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import textwrap
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from saathi import android

ENV_NAMES = (
    "SAATHI_DATA_DIR",
    "SAATHI_AUDIO",
    "SAATHI_AI_CLIENT",
    "SAATHI_SCREEN_HOST",
    "SAATHI_SCREEN_PORT",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "YOUTUBE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_API_KEY",
    "TWILIO_API_SECRET",
    "TWILIO_FROM_NUMBER",
    "TWILIO_TEST_NUMBER",
)

GCP_JSON = json.dumps({"type": "service_account", "project_id": "fake", "private_key": "x"})

FAKE_KEYS = {
    "OPENAI_API_KEY": "",  # blank in the settings screen: not set
    "GROQ_API_KEY": "fake-groq",
    "YOUTUBE_API_KEY": "fake-youtube",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": GCP_JSON,
    "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
    "TWILIO_API_KEY": None,  # a null from Kotlin: not set either
}


class FakeCascadeSession:
    instances: list["FakeCascadeSession"] = []

    def __init__(self, sink_id, **kwargs) -> None:
        self.sink_id = sink_id
        self.kwargs = kwargs
        FakeCascadeSession.instances.append(self)

    def on_intent(self, callback) -> None:
        self.intent = callback

    def start(self) -> None:
        pass

    def send_audio(self, chunk: bytes) -> None:
        pass

    def end_turn(self) -> str:
        return ""

    def say(self, text: str) -> None:
        pass

    def interrupt(self) -> None:
        pass


@pytest.fixture
def entry(monkeypatch, tmp_path):
    """A clean environment, the fake session, and `stop()` afterwards
    whatever the test did -- nothing may outlive its test."""
    from saathi.voice.engine import cascade

    monkeypatch.setattr(cascade, "CascadeSession", FakeCascadeSession)
    FakeCascadeSession.instances.clear()
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert android._running is None
    yield tmp_path / "files"
    android.stop()


@pytest.fixture
def closed(monkeypatch):
    """Every `IdentityStore.close()` call, in order -- the store's
    connection lives on the server thread, so the test thread cannot
    ask the connection itself whether it is closed."""
    from saathi.identity.store import IdentityStore

    calls: list[IdentityStore] = []
    original = IdentityStore.close

    def record(self) -> None:
        calls.append(self)
        original(self)

    monkeypatch.setattr(IdentityStore, "close", record)
    return calls


def _get(url: str) -> tuple[int, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5.0) as response:
        return response.status, response.read().decode()


def _port_is_closed(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(2.0)
        try:
            probe.connect(("127.0.0.1", port))
        except ConnectionRefusedError:
            return True
        return False


def _engine_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "saathi-engine" and t.is_alive()]


def _listening_socket() -> socket.socket:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


# -- start ------------------------------------------------------------------


async def test_start_serves_the_face_and_the_websocket_and_sets_the_environment(entry):
    data_dir = entry
    started = android.start({"data_dir": str(data_dir), "port": 0, "keys": FAKE_KEYS})
    port = started["port"]
    assert started["host"] == "127.0.0.1" and port != 0
    assert started["url"] == f"http://127.0.0.1:{port}"

    status, body = _get(started["url"] + "/")
    assert status == 200 and "face-container" in body

    async with aiohttp.ClientSession() as client:
        async with client.ws_connect(started["url"] + "/ws") as ws:
            assert await ws.receive_json() == {"type": "state", "state": "sleeping"}
            settings = await ws.receive_json()
            assert settings["type"] == "settings"
            assert "piper" in {b["id"] for b in settings["backends"]}
            assert settings["current_backend"] == "google-chirp3-hd"  # read from the store

    # The environment the engine reads, exactly: the mode, the client,
    # the data dir, and only the keys that had a value.
    assert os.environ["SAATHI_DATA_DIR"] == str(data_dir)
    assert os.environ["SAATHI_AUDIO"] == "remote"
    assert os.environ["SAATHI_AI_CLIENT"] == "rest"
    assert os.environ["SAATHI_SCREEN_HOST"] == "127.0.0.1"
    assert os.environ["SAATHI_SCREEN_PORT"] == str(port)
    assert os.environ["GROQ_API_KEY"] == "fake-groq"
    assert os.environ["YOUTUBE_API_KEY"] == "fake-youtube"
    assert os.environ["TWILIO_ACCOUNT_SID"] == "AC" + "0" * 32
    assert "OPENAI_API_KEY" not in os.environ and "TWILIO_API_KEY" not in os.environ
    # The JSON went to a file, not into the environment.
    assert "GOOGLE_APPLICATION_CREDENTIALS_JSON" not in os.environ
    gcp = data_dir / "gcp.json"
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(gcp)
    assert gcp.read_text() == GCP_JSON
    assert stat.S_IMODE(gcp.stat().st_mode) == 0o600
    # The identity database is under the data dir too.
    assert (data_dir / "identity.sqlite3").exists()

    # The runtime was built in remote mode, on the real build_runtime():
    # the session on the "remote" sink, with the notes a person would see.
    assert FakeCascadeSession.instances[-1].sink_id == "remote"
    assert any("SAATHI_AUDIO=remote" in note for note in started["notes"])
    assert any(note.startswith("Calling off") for note in started["notes"])


def test_stop_closes_the_port_restores_the_environment_and_a_second_start_works(entry):
    data_dir = entry
    first = android.start({"data_dir": str(data_dir), "port": 0, "keys": FAKE_KEYS})
    port = first["port"]
    assert not _port_is_closed(port)

    android.stop()
    assert _port_is_closed(port)
    assert _engine_threads() == []
    assert android._running is None
    for name in ENV_NAMES:
        assert name not in os.environ, name
    # The credential file is the data dir's, not the environment's: it
    # stays for the next start to replace or remove.
    assert (data_dir / "gcp.json").exists()

    # A second start on the very same port: the port was released, not
    # merely refused. The explicit port exercises the default-port path
    # the phone takes.
    second = android.start({"data_dir": str(data_dir), "port": port, "keys": FAKE_KEYS})
    assert second["port"] == port
    assert _get(second["url"] + "/")[0] == 200
    assert os.environ["SAATHI_SCREEN_PORT"] == str(port)
    android.stop()
    assert _port_is_closed(port)
    android.stop()  # a no-op when nothing is running, not an error


def test_the_defaults_are_loopback_and_8765_and_a_java_null_port_is_the_default(entry):
    # Not started: 8765 may be in use on the box running the suite. The
    # config reader is what the defaults live in.
    host, port, data_dir, keys = android._read_config({"data_dir": str(entry), "port": None})
    assert (host, port) == ("127.0.0.1", 8765)
    assert data_dir == entry and keys == {}


def test_start_while_running_is_refused_and_leaves_the_first_running(entry):
    started = android.start({"data_dir": str(entry), "port": 0, "keys": FAKE_KEYS})
    with pytest.raises(RuntimeError, match="already running"):
        android.start({"data_dir": str(entry), "port": 0, "keys": FAKE_KEYS})
    assert _get(started["url"] + "/")[0] == 200
    assert len(_engine_threads()) == 1


def test_without_any_ai_key_the_face_still_comes_up_on_the_real_runtime(entry):
    keys = {"YOUTUBE_API_KEY": "fake-youtube"}
    started = android.start({"data_dir": str(entry), "port": 0, "keys": keys})
    assert _get(started["url"] + "/")[0] == 200
    assert any("No AI provider" in note for note in started["notes"])
    assert FakeCascadeSession.instances == []  # no session was built at all
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
    assert not (entry / "gcp.json").exists()


# -- the Google credential ----------------------------------------------------


def test_a_removed_google_credential_removes_the_file_and_a_bad_one_is_noted(entry):
    with_json = dict(FAKE_KEYS)
    android.start({"data_dir": str(entry), "port": 0, "keys": with_json})
    gcp = entry / "gcp.json"
    assert gcp.exists()
    android.stop()

    without = dict(FAKE_KEYS, GOOGLE_APPLICATION_CREDENTIALS_JSON="")
    android.start({"data_dir": str(entry), "port": 0, "keys": without})
    assert not gcp.exists()
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
    android.stop()

    broken = dict(FAKE_KEYS, GOOGLE_APPLICATION_CREDENTIALS_JSON="{not json")
    started = android.start({"data_dir": str(entry), "port": 0, "keys": broken})
    assert not gcp.exists()
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
    assert any("not valid JSON" in note for note in started["notes"])
    assert _get(started["url"] + "/")[0] == 200  # degraded, not dead


def test_an_existing_world_readable_file_is_tightened_to_0600(entry):
    entry.mkdir(parents=True)
    gcp = entry / "gcp.json"
    gcp.write_text("{}")
    gcp.chmod(0o644)
    android.start({"data_dir": str(entry), "port": 0, "keys": FAKE_KEYS})
    assert gcp.read_text() == GCP_JSON
    assert stat.S_IMODE(gcp.stat().st_mode) == 0o600


# -- a config that cannot be used -----------------------------------------------


def test_an_unknown_key_name_is_refused_before_anything_starts(entry):
    keys = dict(FAKE_KEYS, SAATHI_AUDIO="local")
    with pytest.raises(ValueError, match="'SAATHI_AUDIO' is not one the engine reads"):
        android.start({"data_dir": str(entry), "port": 0, "keys": keys})
    assert android._running is None and _engine_threads() == []
    for name in ENV_NAMES:
        assert name not in os.environ, name
    assert not entry.exists()  # nothing was even created


def test_a_config_without_a_data_dir_or_with_a_bad_port_is_refused(entry):
    with pytest.raises(ValueError, match="data_dir"):
        android.start({"port": 0})
    with pytest.raises(ValueError, match="port"):
        android.start({"data_dir": str(entry), "port": "eight"})
    with pytest.raises(ValueError, match="port"):
        android.start({"data_dir": str(entry), "port": 70000})
    assert android._running is None


def test_a_taken_port_is_a_plain_error_with_nothing_left_behind(entry):
    taken = _listening_socket()
    try:
        port = taken.getsockname()[1]
        with pytest.raises(RuntimeError, match=f"cannot listen on 127.0.0.1:{port}"):
            android.start({"data_dir": str(entry), "port": port, "keys": FAKE_KEYS})
    finally:
        taken.close()
    assert android._running is None and _engine_threads() == []
    for name in ENV_NAMES:
        assert name not in os.environ, name


def test_a_server_that_never_answers_is_torn_down_and_reported(entry, monkeypatch, closed):
    monkeypatch.setattr(android, "READY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(android, "_answers", lambda url: False)
    with pytest.raises(RuntimeError, match=r"did not answer GET http://127.0.0.1:\d+/ within 0 s"):
        android.start({"data_dir": str(entry), "port": 0, "keys": FAKE_KEYS})
    assert android._running is None and _engine_threads() == []
    for name in ENV_NAMES:
        assert name not in os.environ, name
    # The store was closed with the rest, on the thread that opened it.
    assert closed == [FakeCascadeSession.instances[-1].kwargs["identity_store"]]


def test_a_server_that_dies_on_its_own_thread_is_reported_with_its_reason(entry, monkeypatch):
    import saathi.screen.server as server_module

    # Not a web.Application: the runner refuses it on the server thread.
    monkeypatch.setattr(server_module, "build_app", lambda core, **kwargs: object())
    with pytest.raises(RuntimeError, match="stopped before it answered.*web.Application"):
        android.start({"data_dir": str(entry), "port": 0, "keys": FAKE_KEYS})
    assert android._running is None and _engine_threads() == []
    assert "SAATHI_AUDIO" not in os.environ


# -- stop --------------------------------------------------------------------------


def test_stop_shuts_down_calling_and_closes_the_store(entry, closed):
    android.start({"data_dir": str(entry), "port": 0, "keys": FAKE_KEYS})
    runtime = android._running.runtime
    assert runtime.calling is None  # remote mode: cli.py never builds it on a phone
    shutdowns = []
    # Were calling ever built on this path, stop() must stop it with the
    # rest: the contract is "stop what was started".
    controller = SimpleNamespace(shutdown=lambda: shutdowns.append(threading.current_thread()))
    runtime.calling = SimpleNamespace(controller=controller)
    assert closed == []
    android.stop()
    assert closed == [runtime.store]
    # Both on the server thread, where the store's connection lives.
    assert [t.name for t in shutdowns] == ["saathi-engine"]


def test_stop_restores_what_the_environment_had_before(entry, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "the-laptops-own")
    monkeypatch.setenv("SAATHI_AI_CLIENT", "sdk")
    android.start({"data_dir": str(entry), "port": 0, "keys": FAKE_KEYS})
    assert os.environ["GROQ_API_KEY"] == "fake-groq"
    assert os.environ["SAATHI_AI_CLIENT"] == "rest"
    android.stop()
    assert os.environ["GROQ_API_KEY"] == "the-laptops-own"
    assert os.environ["SAATHI_AI_CLIENT"] == "sdk"


# -- import ----------------------------------------------------------------------------


def test_importing_the_module_has_no_side_effects(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    probe = textwrap.dedent(
        """
        import json, os, sys, threading
        env_before = dict(os.environ)
        import saathi.android
        print(json.dumps({
            "env_changed": sorted(set(env_before.items()) ^ set(os.environ.items())),
            "threads": threading.active_count(),
            "engine": [m for m in ("aiohttp", "saathi.cli", "saathi.screen.server",
                                   "saathi.core", "saathi.config") if m in sys.modules],
            "running": saathi.android._running,
        }))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report == {"env_changed": [], "threads": 1, "engine": [], "running": None}
    assert list(home.iterdir()) == []  # nothing written anywhere
