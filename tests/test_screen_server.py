import asyncio
import tempfile
import threading
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.core import Core, State
from saathi.identity.store import IdentityStore
from saathi.screen.server import build_app


class FakeSession:
    """Stands in for a real VoiceSession: no Groq, no Piper, no audio."""

    def __init__(self) -> None:
        self.start_calls = 0
        self.interrupt_calls = 0
        self.spoken: list[str] = []

    def start(self) -> None:
        self.start_calls += 1

    def send_audio(self, chunk: bytes) -> None:
        pass

    def end_turn(self) -> str:
        return "reply"

    def say(self, text: str) -> None:
        self.spoken.append(text)

    def interrupt(self) -> None:
        self.interrupt_calls += 1


async def _connect(ws) -> dict:
    """Every fresh connection now gets a `state` message followed by a
    `settings` message (see server.py's module docstring) — tests that
    only care about state drain and sanity-check the settings message
    once here, rather than re-deriving its exact shape at every call
    site."""
    state_message = await ws.receive_json()
    settings_message = await ws.receive_json()
    assert settings_message["type"] == "settings"
    return state_message


class FakeCapture:
    """Stands in for `audio.capture.Capture`: no real `parec` process."""

    instances: list["FakeCapture"] = []

    def __init__(self, source_id, on_chunk, chunk_bytes) -> None:
        self.source_id = source_id
        self.started = False
        self.stopped = False
        FakeCapture.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


async def test_index_serves_the_page():
    async with TestClient(TestServer(build_app(Core()))) as client:
        response = await client.get("/")
        assert response.status == 200
        assert "face-container" in await response.text()


async def test_spacebar_input_drives_state_over_the_websocket():
    core = Core()
    async with TestClient(TestServer(build_app(core))) as client:
        async with client.ws_connect("/ws") as ws:
            first = await _connect(ws)
            assert first == {"type": "state", "state": "sleeping"}

            await ws.send_json({"type": "input", "event": "press"})
            pressed = await ws.receive_json()
            assert pressed == {"type": "state", "state": "listening"}

            await ws.send_json({"type": "input", "event": "release"})
            thinking = await ws.receive_json()
            idle = await ws.receive_json()
            assert thinking == {"type": "state", "state": "thinking"}
            assert idle == {"type": "state", "state": "idle"}

    assert core.state.value == "idle"


async def test_a_second_client_sees_the_same_state_change():
    core = Core()
    async with TestClient(TestServer(build_app(core))) as client:
        async with client.ws_connect("/ws") as first_ws, client.ws_connect("/ws") as second_ws:
            await _connect(first_ws)
            await _connect(second_ws)

            await first_ws.send_json({"type": "input", "event": "press"})

            assert await first_ws.receive_json() == {"type": "state", "state": "listening"}
            assert await second_ws.receive_json() == {"type": "state", "state": "listening"}


async def test_press_while_speaking_is_barge_in(monkeypatch):
    # Checkpoint 2: press during SPEAKING is now a real transition
    # (core.py) — this is what it must actually do: stop the current
    # reply and start listening, the same as any other press. Was a
    # no-op through the one-hour spike; see core.py's module docstring.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    core = Core(initial=State.SPEAKING)
    session = FakeSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            first = await _connect(ws)
            assert first == {"type": "state", "state": "speaking"}

            await ws.send_json({"type": "input", "event": "press"})
            listening = await ws.receive_json()
            assert listening == {"type": "state", "state": "listening"}

    assert core.state == State.LISTENING
    assert session.interrupt_calls == 1
    assert session.start_calls == 1
    assert len(FakeCapture.instances) == 1
    assert FakeCapture.instances[0].started is True


async def test_barge_in_supersedes_the_interrupted_turns_stale_completion(monkeypatch):
    # The race the turn-generation counter exists for: a turn that's
    # still unwinding (blocked in say(), in its own executor thread) when
    # a barge-in press arrives must not fire `done` once it finally
    # returns — core.py already moved on from that press, not from this
    # turn's tail end.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    say_blocked = threading.Event()
    say_may_return = threading.Event()

    class SlowSession(FakeSession):
        def say(self, text: str) -> None:
            say_blocked.set()
            say_may_return.wait(timeout=2.0)

        def interrupt(self) -> None:
            super().interrupt()
            say_may_return.set()  # a real interrupt() kills what say() was blocked on

    core = Core()
    session = SlowSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            assert await _connect(ws) == {"type": "state", "state": "sleeping"}

            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}

            # Don't barge in until the turn is actually blocked in say(),
            # or this test doesn't exercise the race it's named for.
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, say_blocked.wait, 2.0)

            await ws.send_json({"type": "input", "event": "press"})
            listening_again = await ws.receive_json()
            assert listening_again == {"type": "state", "state": "listening"}

            # Give the superseded turn's executor thread time to actually
            # return from say() and attempt (and fail) to fire `done`.
            await asyncio.sleep(0.2)

    assert core.state == State.LISTENING  # not IDLE — the stale `done` must not land
    assert session.interrupt_calls == 1


async def test_a_real_turn_walks_thinking_speaking_idle_and_captures_audio(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    core = Core()
    session = FakeSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            assert await _connect(ws) == {"type": "state", "state": "sleeping"}

            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}

            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}

    assert core.state == State.IDLE
    assert session.start_calls == 1
    assert len(FakeCapture.instances) == 1
    assert FakeCapture.instances[0].started is True
    assert FakeCapture.instances[0].stopped is True


async def test_a_failed_turn_speaks_a_short_fallback_then_goes_idle(monkeypatch):
    # Robustness pass: an upstream failure (401/429/timeout/connection
    # reset from Groq, simulated here as any exception from end_turn())
    # used to go straight from THINKING to IDLE via no_response, silent —
    # exactly the "say something short out loud" gap SPEC.md already
    # calls out for slow turns, just not previously wired for failed ones.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    class FailingSession(FakeSession):
        def end_turn(self) -> str:
            raise RuntimeError("Groq is down")

    core = Core()
    session = FailingSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            assert await _connect(ws) == {"type": "state", "state": "sleeping"}

            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}

            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}

    assert core.state == State.IDLE
    assert len(session.spoken) == 1
    assert session.spoken[0]  # something was said, not silence


async def test_a_turn_that_also_fails_to_speak_the_fallback_still_reaches_idle(monkeypatch):
    # Piper is local and doesn't depend on whatever just failed, but the
    # state machine must not get stuck even if speaking the fallback
    # itself somehow raises too.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    class DoublyFailingSession(FakeSession):
        def end_turn(self) -> str:
            raise RuntimeError("Groq is down")

        def say(self, text: str) -> None:
            raise RuntimeError("Piper is down too")

    core = Core()
    session = DoublyFailingSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            assert await _connect(ws) == {"type": "state", "state": "sleeping"}
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}

    assert core.state == State.IDLE

    assert core.state == State.IDLE


# -- settings panel (item C/G): settings message + set_preference -------


def _tmp_store() -> IdentityStore:
    tmp_dir = tempfile.mkdtemp()
    store = IdentityStore(Path(tmp_dir) / "identity.sqlite3")
    store.create()
    return store


async def test_settings_message_on_connect_lists_languages_and_backends():
    async with TestClient(TestServer(build_app(Core()))) as client:
        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # state
            settings = await ws.receive_json()

    assert settings["type"] == "settings"
    assert "english" in settings["languages"]
    assert settings["current_language"] == "english"
    backend_ids = {b["id"] for b in settings["backends"]}
    assert "piper" in backend_ids
    piper = next(b for b in settings["backends"] if b["id"] == "piper")
    assert piper["available"] is True
    assert piper["cost_per_million_chars_usd"] == 0.0
    assert settings["current_backend"] == "piper"


async def test_set_preference_without_a_store_reports_failure_not_a_crash():
    async with TestClient(TestServer(build_app(Core()))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "set_preference", "key": "language", "value": "chinese"})
            result = await ws.receive_json()

    assert result == {
        "type": "preference_result",
        "key": "language",
        "ok": False,
        "reason": "no identity store configured on this run",
    }


async def test_set_preference_with_a_store_succeeds_and_broadcasts_new_settings():
    store = _tmp_store()
    app = build_app(Core(), store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as first_ws, client.ws_connect("/ws") as second_ws:
            await _connect(first_ws)
            await _connect(second_ws)

            await first_ws.send_json(
                {"type": "set_preference", "key": "tts_backend", "value": "piper"}
            )
            result = await first_ws.receive_json()
            assert result == {
                "type": "preference_result",
                "key": "tts_backend",
                "ok": True,
                "reason": "",
            }

            # Every open connection sees the updated settings, not just the
            # one that made the change -- the panel isn't the only client
            # that should reflect a preference someone else just set.
            first_broadcast = await first_ws.receive_json()
            second_broadcast = await second_ws.receive_json()
            assert first_broadcast["type"] == "settings"
            assert first_broadcast["current_backend"] == "piper"
            assert second_broadcast == first_broadcast

    store.close()


async def test_set_preference_twice_for_the_same_key_is_a_named_failure_not_a_crash():
    # Documents the real limitation raised in chat: preferences.key is a
    # PRIMARY KEY under the current schema, so append() (IdentityStore's
    # only write primitive) succeeds once per key and raises after that.
    # write_preference() turns that into PreferenceLocked rather than
    # letting an unhandled IntegrityError take the websocket down -- see
    # saathi/identity/preferences.py's docstring for the proposed fix.
    store = _tmp_store()
    app = build_app(Core(), store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)

            await ws.send_json({"type": "set_preference", "key": "language", "value": "chinese"})
            first_result = await ws.receive_json()
            assert first_result["ok"] is True
            await ws.receive_json()  # the settings broadcast that follows

            await ws.send_json({"type": "set_preference", "key": "language", "value": "hindi"})
            second_result = await ws.receive_json()
            assert second_result["ok"] is False
            assert "language" in second_result["reason"]

    store.close()


async def test_set_preference_rejects_an_unknown_key_or_non_string_value():
    store = _tmp_store()
    app = build_app(Core(), store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)

            await ws.send_json({"type": "set_preference", "key": "volume", "value": "11"})
            await ws.send_json(
                {"type": "set_preference", "key": "language", "value": ["chinese"]}
            )
            # Neither malformed message gets a response; a well-formed one
            # right after does, proving the connection is still alive and
            # the bad messages were dropped, not silently queued.
            await ws.send_json({"type": "set_preference", "key": "language", "value": "hindi"})
            result = await ws.receive_json()
            assert result["ok"] is True

    store.close()
