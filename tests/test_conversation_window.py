"""screen/server.py's open conversation (2026-10-08): after a spoken
reply the eyes stay on her and the mic listens without the name; a quiet
window closes it. And a turn that was only her name waits, then says
"Yes?". Real ListenWindow and Endpointer, an injected speech detector,
fake captures and sessions -- no audio, no cloud."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.audio.listen_window import ListenWindow
from saathi.audio.vad import CHUNK_BYTES
from saathi.audio.wake import Endpointer
from saathi.core import Core, State
from saathi.screen.server import build_app
from tests.test_screen_server import ChunkCapture, RecordingSession, _connect

QUIET = b"\x00" * CHUNK_BYTES
SPEECH = b"\x01" * CHUNK_BYTES


def _is_speech(chunk: bytes) -> bool:
    return chunk[:1] == b"\x01"


def _window(seconds: float) -> ListenWindow:
    return ListenWindow(
        _is_speech,
        window_seconds=seconds,
        guard_seconds=0.0,
        onset_chunks=2,
        endpointer=Endpointer(_is_speech, silence_seconds=0.1),
    )


class NameOnlySession(RecordingSession):
    """The first turn is only her name; later ones are ordinary."""

    def __init__(self) -> None:
        super().__init__()
        self.turns = 0
        self.heard_only_name = False

    def end_turn(self) -> str:
        self.turns += 1
        self.heard_only_name = self.turns == 1
        return "" if self.heard_only_name else "reply"

    def name_prompt(self) -> str:
        return "Yes?"


async def _next_state(ws) -> str:
    while True:
        message = await ws.receive_json()
        if message["type"] == "state":
            return message["state"]


async def _states(ws, n: int) -> list[str]:
    return [await _next_state(ws) for _ in range(n)]


def _app(session, **kwargs):
    kwargs.setdefault("follow_up_seconds", 0.5)
    return build_app(
        Core(initial=State.IDLE),
        session=session,
        capture_source_id="src",
        listen_window=_window,
        **kwargs,
    )


def _feed(capture, *chunks) -> None:
    for chunk in chunks:
        capture.on_chunk(chunk)


async def test_after_a_reply_she_can_follow_up_without_the_name(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = RecordingSession()
    async with TestClient(TestServer(_app(session))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            # The reply ends with her still being looked at, not at idle.
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "attentive"]
            window = ChunkCapture.instances[-1]
            assert window.started and not window.stopped
            _feed(window, QUIET, SPEECH, SPEECH)  # she starts talking
            assert await _next_state(ws) == "listening"
            _feed(window, SPEECH, *[QUIET] * 6)  # ...and stops
            assert await _states(ws, 3) == ["thinking", "speaking", "attentive"]
            assert window.stopped
            # What reached the session is her follow-up, preroll included.
            assert b"".join(session.audio).count(SPEECH) == 3
            assert session.spoken == ["reply", "reply"]
            # A quiet window closes the conversation: the name again.
            _feed(ChunkCapture.instances[-1], *[QUIET] * 20)
            assert await _next_state(ws) == "idle"
    assert ChunkCapture.instances[-1].stopped


async def test_a_quiet_window_sends_nothing_to_the_session(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = RecordingSession()
    async with TestClient(TestServer(_app(session))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            assert (await _states(ws, 4))[-1] == "attentive"
            starts = session.start_calls
            _feed(ChunkCapture.instances[-1], *[QUIET] * 20)
            assert await _next_state(ws) == "idle"
    assert session.start_calls == starts  # never opened a turn


async def test_the_spacebar_still_works_inside_the_window(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = RecordingSession()
    async with TestClient(TestServer(_app(session))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            assert (await _states(ws, 4))[-1] == "attentive"
            window = ChunkCapture.instances[-1]
            await ws.send_json({"type": "input", "event": "press"})
            assert await _next_state(ws) == "listening"
            assert window.stopped  # the window's mic is let go
            assert ChunkCapture.instances[-1] is not window
            await ws.send_json({"type": "input", "event": "release"})
            assert await _states(ws, 3) == ["thinking", "speaking", "attentive"]


async def test_no_window_after_a_media_command_while_the_song_plays(monkeypatch):
    from saathi.tools.media import MediaController, MediaResult

    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    media = MediaController(search=lambda q: [MediaResult(1, "v1", "Song")])
    media.handle("search", query="song")
    media.handle("play", choice=1)

    class LouderSession(RecordingSession):
        def end_turn(self) -> str:
            media.handle("louder")  # through the broadcast seam, like a tool
            return "Louder."

    session = LouderSession()
    async with TestClient(TestServer(_app(session, media=media))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            # The song at its new level is the answer; no ducked window.
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "idle"]


async def test_her_name_alone_waits_then_says_yes_then_listens(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = NameOnlySession()
    app = _app(session, name_prompt_seconds=0.5)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            # Only her name: no reply, eyes on her, still listening.
            assert await _states(ws, 3) == ["listening", "thinking", "attentive"]
            assert session.spoken == []
            _feed(ChunkCapture.instances[-1], *[QUIET] * 20)  # she stays quiet
            assert await _states(ws, 2) == ["speaking", "attentive"]
            assert session.spoken == ["Yes?"]
            # Then the ordinary open window: she answers without the name.
            window = ChunkCapture.instances[-1]
            _feed(window, SPEECH, SPEECH, SPEECH, *[QUIET] * 6)
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "attentive"]
            assert session.spoken == ["Yes?", "reply"]


async def test_her_name_then_the_request_after_a_pause_is_one_conversation(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = NameOnlySession()
    async with TestClient(TestServer(_app(session, name_prompt_seconds=2.0))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            assert (await _states(ws, 3))[-1] == "attentive"
            _feed(ChunkCapture.instances[-1], QUIET, SPEECH, SPEECH, *[QUIET] * 6)
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "attentive"]
            assert session.spoken == ["reply"]  # no "Yes?": she went on


async def test_without_follow_up_seconds_a_reply_still_ends_at_idle(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    session = RecordingSession()
    async with TestClient(TestServer(_app(session, follow_up_seconds=None))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "idle"]
    await asyncio.sleep(0)


async def test_after_her_name_and_a_long_silence_yes_comes_at_once(monkeypatch):
    # The wake turn already waited out the quiet (its endpointer's
    # no-speech limit): "Yes?" is said straight away, not after another
    # wait. The wake path, with the turn's own endpointer faked.
    from tests.test_screen_server import FakeEndpointer, FakeWake

    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    monkeypatch.setattr(server_module, "_ATTENTIVE_SECONDS", 0.01)
    ChunkCapture.instances.clear()
    wake, session = FakeWake(), NameOnlySession()
    app = _app(session, wake=wake, wake_endpointer=FakeEndpointer, name_prompt_seconds=0.05)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            wake.on_wake(b"saathi", "")
            assert await _states(ws, 2) == ["attentive", "listening"]
            await asyncio.sleep(0.1)  # she says nothing
            ChunkCapture.instances[-1].on_chunk(b"END")
            assert await _states(ws, 4) == ["thinking", "attentive", "speaking", "attentive"]
            assert session.spoken == ["Yes?"]


async def test_with_open_after_reply_off_a_reply_ends_at_idle(monkeypatch):
    # A video in the room was answered turn after turn: only her name
    # starts a turn, so an ordinary reply closes the conversation.
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = RecordingSession()
    async with TestClient(TestServer(_app(session, open_after_reply=False))) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "idle"]
            assert session.spoken == ["reply"]
    await asyncio.sleep(0)


async def test_with_open_after_reply_off_yes_after_her_name_still_listens(monkeypatch):
    # "Saathi" ... "Yes?": she has just addressed Saathi, so her answer
    # needs no name -- but the reply to it closes the conversation.
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = NameOnlySession()
    app = _app(session, name_prompt_seconds=0.5, open_after_reply=False)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            assert await _states(ws, 3) == ["listening", "thinking", "attentive"]
            _feed(ChunkCapture.instances[-1], *[QUIET] * 20)
            assert await _states(ws, 2) == ["speaking", "attentive"]
            _feed(ChunkCapture.instances[-1], SPEECH, SPEECH, SPEECH, *[QUIET] * 6)
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "idle"]
            assert session.spoken == ["Yes?", "reply"]
