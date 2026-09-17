import asyncio

from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.core import Core, State
from saathi.screen.server import build_app


class FakeSession:
    """Stands in for a real VoiceSession: no Groq, no Piper, no audio."""

    def __init__(self) -> None:
        self.start_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def send_audio(self, chunk: bytes) -> None:
        pass

    def end_turn(self) -> str:
        return "reply"

    def say(self, text: str) -> None:
        pass


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
            first = await ws.receive_json()
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
            await first_ws.receive_json()
            await second_ws.receive_json()

            await first_ws.send_json({"type": "input", "event": "press"})

            assert await first_ws.receive_json() == {"type": "state", "state": "listening"}
            assert await second_ws.receive_json() == {"type": "state", "state": "listening"}


async def test_press_while_speaking_starts_no_second_capture(monkeypatch):
    # The exact bug found live during the one-hour spike: pressing space
    # while Saathi is still speaking must not start a second capture —
    # `press` has no transition out of SPEAKING, so core.py correctly
    # no-ops it, and the server must act on that, not on the press alone.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    core = Core(initial=State.SPEAKING)
    session = FakeSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            first = await ws.receive_json()
            assert first == {"type": "state", "state": "speaking"}

            await ws.send_json({"type": "input", "event": "press"})
            await asyncio.sleep(0.05)  # let the (silent, no-broadcast) no-op land

    assert core.state == State.SPEAKING
    assert session.start_calls == 0
    assert FakeCapture.instances == []


async def test_a_real_turn_walks_thinking_speaking_idle_and_captures_audio(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    core = Core()
    session = FakeSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            assert await ws.receive_json() == {"type": "state", "state": "sleeping"}

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


async def test_a_turn_that_raises_goes_to_idle_via_no_response(monkeypatch):
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
            assert await ws.receive_json() == {"type": "state", "state": "sleeping"}

            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}

            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}

    assert core.state == State.IDLE
