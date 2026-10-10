import asyncio
import io
import json
import logging
import tempfile
import threading
import time
import wave
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.audio.remote import RemoteAudio
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
    # No preference stored: the panel shows the preferred default (Chirp),
    # the same thing cli.py hands the session, not the offline fallback.
    assert settings["current_backend"] == "google-chirp3-hd"


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


async def test_set_preference_twice_for_the_same_key_both_succeed():
    # preferences is an append-only log (2026-09-18 schema) -- a second
    # write to the same key used to raise (PreferenceLocked, via a
    # PRIMARY KEY on preferences.key), found live to break exactly this:
    # changing a language or backend preference a second time.
    store = _tmp_store()
    app = build_app(Core(), store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)

            await ws.send_json({"type": "set_preference", "key": "language", "value": "chinese"})
            first_result = await ws.receive_json()
            assert first_result["ok"] is True
            first_settings = await ws.receive_json()
            assert first_settings["current_language"] == "chinese"

            await ws.send_json({"type": "set_preference", "key": "language", "value": "hindi"})
            second_result = await ws.receive_json()
            assert second_result["ok"] is True
            second_settings = await ws.receive_json()
            assert second_settings["current_language"] == "hindi"

    store.close()

    store.close()


async def test_set_preference_rejects_an_unknown_key_or_non_string_value():
    store = _tmp_store()
    app = build_app(Core(), store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)

            await ws.send_json({"type": "set_preference", "key": "volume", "value": "11"})
            await ws.send_json({"type": "set_preference", "key": "language", "value": ["chinese"]})
            # Neither malformed message gets a response; a well-formed one
            # right after does, proving the connection is still alive and
            # the bad messages were dropped, not silently queued.
            await ws.send_json({"type": "set_preference", "key": "language", "value": "hindi"})
            result = await ws.receive_json()
            assert result["ok"] is True

    store.close()


# -- turn logging (item E) -----------------------------------------------


class TimedFakeSession(FakeSession):
    """A FakeSession that also offers the additive
    `pop_last_turn_timings()` capability real CascadeSession has (see
    cascade.py's `TurnTimings`) -- server.py reads it via getattr, same
    pattern as `preload()`, so a fake exercising it needs nothing beyond
    just having the method."""

    def __init__(self, timings) -> None:
        super().__init__()
        self._timings = timings

    def pop_last_turn_timings(self):
        return self._timings


class _Timings:
    def __init__(
        self,
        stt_ms,
        first_token_ms,
        first_tts_chunk_ms,
        prompt_tokens=None,
        completion_tokens=None,
        cost_usd=None,
        voice=None,
        tts_chars=None,
        tts_cost_usd=None,
    ):
        self.stt_ms = stt_ms
        self.first_token_ms = first_token_ms
        self.first_tts_chunk_ms = first_tts_chunk_ms
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd
        self.voice = voice
        self.tts_chars = tts_chars
        self.tts_cost_usd = tts_cost_usd


async def _run_one_full_turn(ws, capture_ms=0):
    await ws.send_json({"type": "input", "event": "press"})
    assert await ws.receive_json() == {"type": "state", "state": "listening"}
    if capture_ms:
        await asyncio.sleep(capture_ms / 1000)
    await ws.send_json({"type": "input", "event": "release"})
    assert await ws.receive_json() == {"type": "state", "state": "thinking"}
    assert await ws.receive_json() == {"type": "state", "state": "speaking"}
    assert await ws.receive_json() == {"type": "state", "state": "idle"}


async def test_a_completed_turn_is_logged_with_real_timings(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    store = _tmp_store()
    session = TimedFakeSession(
        _Timings(
            stt_ms=100,
            first_token_ms=200,
            first_tts_chunk_ms=50,
            prompt_tokens=120,
            completion_tokens=30,
            cost_usd=0.00021,
            voice="warm",
            tts_chars=64,
            tts_cost_usd=0.00192,
        )
    )
    app = build_app(Core(), session=session, capture_source_id="fake-aec-source", store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _run_one_full_turn(ws, capture_ms=20)

    rows = store.read("turns")
    store.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["mode"] == "voice"
    assert row["engine"] == "cascade"
    assert row["stt_ms"] == 100
    assert row["first_token_ms"] == 200
    assert row["first_tts_chunk_ms"] == 50
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 30
    assert row["cost_usd"] == 0.00021
    assert row["voice"] == "warm"
    assert row["tts_chars"] == 64
    assert row["tts_cost_usd"] == 0.00192
    assert row["eou_ms"] is not None and row["eou_ms"] >= 20
    assert row["handoff"] == 0


async def test_a_turn_with_no_store_does_not_crash(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    session = TimedFakeSession(_Timings(stt_ms=10, first_token_ms=10, first_tts_chunk_ms=10))
    app = build_app(Core(), session=session, capture_source_id="fake-aec-source")  # store=None

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _run_one_full_turn(ws)  # must not raise


async def test_a_session_without_pop_last_turn_timings_logs_eou_only(monkeypatch):
    # Plain FakeSession has no pop_last_turn_timings -- the real gap a
    # fake or a future VoiceSession implementation without granular
    # instrumentation would leave; the turn is still logged, just
    # without the per-stage columns, rather than not logged at all.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    store = _tmp_store()
    session = FakeSession()
    app = build_app(Core(), session=session, capture_source_id="fake-aec-source", store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _run_one_full_turn(ws)

    rows = store.read("turns")
    store.close()
    assert len(rows) == 1
    assert rows[0]["stt_ms"] is None
    assert rows[0]["first_token_ms"] is None
    assert rows[0]["first_tts_chunk_ms"] is None
    assert rows[0]["eou_ms"] is not None


async def test_a_superseded_barge_in_turn_is_not_logged(monkeypatch):
    # The turn that got barged into never reaches core.handle(Event("done"))
    # (see _speak_and_finish) -- and _log_turn is called right there, so a
    # superseded turn correctly produces no row at all, not a row with
    # wrong or partial numbers.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)  # no real parec
    store = _tmp_store()
    say_blocked = threading.Event()
    say_may_return = threading.Event()

    class SlowSession(FakeSession):
        def say(self, text: str) -> None:
            say_blocked.set()
            say_may_return.wait(timeout=2.0)

        def interrupt(self) -> None:
            super().interrupt()
            say_may_return.set()

    core = Core()
    session = SlowSession()
    app = build_app(core, session=session, capture_source_id="fake-aec-source", store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, say_blocked.wait, 2.0)

            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await asyncio.sleep(0.2)  # give the superseded turn time to unwind

    rows = store.read("turns")
    store.close()
    assert rows == []


async def test_a_silent_turn_goes_back_to_idle_without_speaking_or_logging(monkeypatch):
    # The silence bug's contract: end_turn() returning "" means nothing
    # was said. THINKING -> IDLE via no_response -- never SPEAKING, no
    # fallback line, no "I didn't catch that", and no `turns` row.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    class SilentSession(FakeSession):
        def end_turn(self) -> str:
            return ""

    store = _tmp_store()
    session = SilentSession()
    core = Core()
    app = build_app(core, session=session, capture_source_id="fake-aec-source", store=store)

    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}

    rows = store.read("turns")
    store.close()
    assert core.state == State.IDLE
    assert session.spoken == []
    assert rows == []


async def test_a_timings_object_predating_the_voice_columns_still_logs_the_row(monkeypatch):
    # A session whose TurnTimings has no voice/tts_chars/tts_cost_usd
    # (a fake, or a future engine) must still land its row: `_log_turn`
    # reads the three via getattr, because its broad except would
    # otherwise drop the whole turn over three nullable columns.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()

    class _OldTimings:
        stt_ms = 10
        first_token_ms = 20
        first_tts_chunk_ms = 30
        prompt_tokens = None
        completion_tokens = None
        cost_usd = None

    store = _tmp_store()
    session = TimedFakeSession(_OldTimings())
    app = build_app(Core(), session=session, capture_source_id="fake-aec-source", store=store)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _run_one_full_turn(ws)
    rows = store.read("turns")
    store.close()
    assert len(rows) == 1
    assert rows[0]["stt_ms"] == 10
    assert rows[0]["voice"] is None

# -- media (YouTube stream): the tool speaks to the screen through the seam --


def _media_fixture_results():
    from saathi.tools.media import parse_search_response

    fixture = Path(__file__).parent / "fixtures" / "youtube_search_old_chinese_songs.json"
    return parse_search_response(json.loads(fixture.read_text()))


class ToolSession(FakeSession):
    """A FakeSession whose end_turn() does what a real CascadeSession does
    inside a turn with a tool call: invokes the registered intent handler
    (cli.py's handle_intent shape -- permission-checked Registry.call)
    from the executor thread, records the result, and returns a reply.
    Queue one call per turn with `queue()`."""

    def __init__(self, handle_intent) -> None:
        super().__init__()
        self._handle_intent = handle_intent
        self._queued: list[dict] = []
        self.results: list[dict] = []

    def queue(self, **arguments) -> None:
        self._queued.append(arguments)

    def end_turn(self) -> str:
        if not self._queued:
            return "reply"
        arguments = self._queued.pop(0)
        result = self._handle_intent("play_music", arguments)
        self.results.append(result)
        return "reply"


def _media_app(granted=frozenset({"music"}), results=None):
    """A build_app wired the way cli.py's diff wires it: a controller,
    the tool registered, the permission granted (or not), and the
    controller handed to build_app so the seam gets installed."""
    from saathi.tools.media import MediaController, make_media_tool
    from saathi.tools.registry import PermissionDenied, Registry, UnknownTool

    found = results if results is not None else _media_fixture_results()
    controller = MediaController(search=lambda _query: found)
    registry = Registry()
    registry.register(make_media_tool(controller))

    def handle_intent(name: str, arguments: dict) -> dict:
        try:
            return registry.call(name, granted, **arguments)
        except UnknownTool:
            return {"status": "error", "detail": f"no such tool: {name}"}
        except PermissionDenied as exc:
            return {"status": "denied", "detail": str(exc)}

    session = ToolSession(handle_intent)
    core = Core()
    app = build_app(core, session=session, capture_source_id="fake-aec-source", media=controller)
    return app, session, controller, found


async def _turn_collecting_media(ws, session, **call) -> list[dict]:
    """One full turn with `call` queued for the tool; returns every
    `media` message that arrived before the turn's `idle`."""
    session.queue(**call)
    await ws.send_json({"type": "input", "event": "press"})
    assert await ws.receive_json() == {"type": "state", "state": "listening"}
    await ws.send_json({"type": "input", "event": "release"})
    assert await ws.receive_json() == {"type": "state", "state": "thinking"}
    media = []
    states = []
    while True:
        message = await ws.receive_json()
        if message["type"] == "media":
            media.append(message)
            continue
        states.append(message["state"])
        if message["state"] == "idle":
            break
    assert states == ["speaking", "idle"]
    return media


async def test_connecting_with_a_media_controller_still_yields_exactly_state_then_settings(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, _ = _media_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _turn_collecting_media(ws, session, action="search", query="old Chinese songs")
            assert controller.last_results  # a search has happened...
        async with client.ws_connect("/ws") as fresh:
            # ...and a fresh connection is still state, settings, nothing
            # else. No `media` on connect, ever.
            assert (await _connect(fresh))["type"] == "state"
            await fresh.send_json({"type": "input", "event": "press"})
            assert await fresh.receive_json() == {"type": "state", "state": "listening"}


async def test_a_search_turn_broadcasts_three_results_to_every_client_and_returns_spoken_titles(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, found = _media_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/ws") as other:
            await _connect(ws)
            await _connect(other)
            media = await _turn_collecting_media(
                ws, session, action="search", query="old Chinese songs"
            )
            seen_by_other = await other.receive_json()
            while seen_by_other["type"] != "media":
                seen_by_other = await other.receive_json()

    assert len(media) == 1
    assert media[0]["action"] == "results"
    assert len(media[0]["results"]) == 3
    assert media[0]["results"][0]["index"] == 1
    assert seen_by_other == media[0]

    result = session.results[0]
    assert result["status"] == "ok"
    assert [r[:6] for r in result["results"]] == ["One: 推", "Two: T", "Three:"]
    assert "note" in result
    json.dumps(result)


async def test_the_second_one_that_one_and_again_resolve_across_turns(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, found = _media_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _turn_collecting_media(ws, session, action="search", query="old Chinese songs")

            # "the second one"
            media = await _turn_collecting_media(ws, session, action="play", choice=2)
            assert media == [
                {
                    "type": "media",
                    "action": "play",
                    "video_id": found[1].video_id,
                    "title": found[1].title,
                    "index": 2,
                    "volume": 70,
                    "fullscreen": False,
                    "target": "embed",
                    "watch_url": f"https://www.youtube.com/watch?v={found[1].video_id}",
                }
            ]
            assert session.results[-1]["playing"].startswith("Two: ")

            # "that one" -- no choice: the one she most recently meant
            media = await _turn_collecting_media(ws, session, action="play")
            assert media[0]["video_id"] == found[1].video_id

            # "play that again"
            media = await _turn_collecting_media(ws, session, action="again")
            assert media[0]["action"] == "play"
            assert media[0]["video_id"] == found[1].video_id

            # "the first one", several turns later
            media = await _turn_collecting_media(ws, session, action="play", choice=1)
            assert media[0]["video_id"] == found[0].video_id


async def test_every_transport_and_layout_action_broadcasts_its_message(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, found = _media_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _turn_collecting_media(ws, session, action="search", query="q")
            await _turn_collecting_media(ws, session, action="play", choice=1)

            expected = {
                "quieter": {"type": "media", "action": "volume", "level": 55},
                "louder": {"type": "media", "action": "volume", "level": 70},
                "pause": {"type": "media", "action": "pause"},
                "resume": {"type": "media", "action": "resume"},
                "bigger": {"type": "media", "action": "layout", "mode": "fullscreen"},
                "smaller": {"type": "media", "action": "layout", "mode": "panel"},
                "stop": {"type": "media", "action": "stop"},
            }
            for action, message in expected.items():
                media = await _turn_collecting_media(ws, session, action=action)
                assert media == [message], action


async def test_without_the_music_permission_the_tool_is_denied_and_nothing_is_shown(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, _ = _media_app(granted=frozenset({"preferences"}))
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            media = await _turn_collecting_media(ws, session, action="search", query="q")
    assert media == []
    assert session.results[0]["status"] == "denied"
    assert controller.last_results == []


async def test_the_browser_reporting_ended_updates_what_carry_on_means(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, found = _media_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _turn_collecting_media(ws, session, action="search", query="q")
            await _turn_collecting_media(ws, session, action="play", choice=3)
            assert controller.playing is True

            await ws.send_json({"type": "media_event", "event": "ended"})
            await ws.send_json({"type": "media_event", "event": 42})  # malformed: dropped
            # A well-formed turn right after proves both were consumed.
            media = await _turn_collecting_media(ws, session, action="resume")

    assert controller.playing is True
    assert media[0]["action"] == "play"  # carrying on after it ended starts it over
    assert media[0]["video_id"] == found[2].video_id


async def test_a_media_message_emitted_off_the_loop_thread_reaches_the_client(monkeypatch):
    # The seam's whole reason to exist: the handler runs in the executor
    # thread, where the socket set must not be touched directly.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    _, session, controller, _ = _media_app()
    thread_names = []

    class ThreadRecordingSession(ToolSession):
        def end_turn(self) -> str:
            thread_names.append(threading.current_thread().name)
            return super().end_turn()

    recording = ThreadRecordingSession(session._handle_intent)
    app = build_app(Core(), session=recording, capture_source_id="fake", media=controller)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            media = await _turn_collecting_media(ws, recording, action="search", query="q")
    assert media and media[0]["action"] == "results"
    assert thread_names and "MainThread" not in thread_names


# -- media + cards: the offer is a card; a tap starts playback ------------


async def test_a_search_offers_a_card_and_a_tap_on_it_broadcasts_play(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    from saathi.screen.cards import CardController
    from saathi.tools.media import MediaController, make_media_tool
    from saathi.tools.registry import Registry

    found = _media_fixture_results()
    cards = CardController()
    controller = MediaController(search=lambda _q: found, cards=cards)
    registry = Registry()
    registry.register(make_media_tool(controller))
    session = ToolSession(lambda name, args: registry.call(name, frozenset({"music"}), **args))
    app = build_app(
        Core(), session=session, capture_source_id="fake", media=controller, cards=cards
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/ws") as other:
            await _connect(ws)
            await _connect(other)
            session.queue(action="search", query="old Chinese songs")
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            seen = []
            message = await ws.receive_json()
            while message != {"type": "state", "state": "idle"}:
                seen.append(message)
                message = await ws.receive_json()
            cards_seen = [m for m in seen if m["type"] == "card"]
            assert [m for m in seen if m["type"] == "media"] == []  # no panel results view
            assert len(cards_seen) == 1
            card = cards_seen[0]["card"]
            assert card["kind"] == "choice" and len(card["options"]) == 3
            assert session.results[0]["spoken"] == card["spoken"]
            while (await other.receive_json()).get("state") != "idle":
                pass

            # She taps the second one on the other screen.
            await other.send_json(
                {"type": "card_answer", "id": card["id"], "answer": {"choice": 2}}
            )
            assert await ws.receive_json() == {"type": "card", "card": None}
            play = await ws.receive_json()
            assert play["type"] == "media" and play["action"] == "play"
            assert play["video_id"] == found[1].video_id
            assert await other.receive_json() == {"type": "card", "card": None}
            assert (await other.receive_json())["action"] == "play"
    assert controller.now_playing == found[1]
    assert cards.current is None


# -- cards: shown by a tool through the seam, answered by tap or voice -----


def _cards_app(hold_seconds=None, on_hold_complete=None):
    from saathi.screen.cards import CardController, HoldController, confirm

    cards = CardController()
    hold = None
    if hold_seconds is not None:
        hold = HoldController(cards)
        hold.set_handler(on_hold_complete or (lambda: None), seconds=hold_seconds, label="Hold")

    class CardSession(FakeSession):
        """end_turn() shows a card, as a tool would from the executor
        thread, and returns what the tool would tell the model to say."""

        shown: list[str] = []

        def end_turn(self) -> str:
            card = confirm("Call Priya, your daughter?")
            CardSession.shown.append(cards.show(card))
            return card.spoken

    session = CardSession()
    CardSession.shown = []
    core = Core()
    app = build_app(
        core,
        session=session,
        capture_source_id="fake-aec-source",
        cards=cards,
        hold=hold,
    )
    return app, session, cards, hold, core


async def _turn_collecting_cards(ws, session) -> list[dict]:
    await ws.send_json({"type": "input", "event": "press"})
    assert await ws.receive_json() == {"type": "state", "state": "listening"}
    await ws.send_json({"type": "input", "event": "release"})
    assert await ws.receive_json() == {"type": "state", "state": "thinking"}
    cards_seen = []
    while True:
        message = await ws.receive_json()
        if message["type"] == "card":
            cards_seen.append(message)
            continue
        if message["state"] == "idle":
            break
    return cards_seen


async def test_connecting_with_cards_and_hold_still_yields_exactly_state_then_settings(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, cards, hold, _ = _cards_app(hold_seconds=None)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _turn_collecting_cards(ws, session)
            assert cards.current is not None  # a card is up...
        async with client.ws_connect("/ws") as fresh:
            # ...and a fresh connection gets state, settings, then the card
            # that is still up: the server holds the question, the browser
            # only draws it, and a reload must not lose it (2026-09-26).
            assert (await _connect(fresh))["type"] == "state"
            resent = await fresh.receive_json()
            assert resent == {"type": "card", "card": cards.current.as_message()}
            await fresh.send_json({"type": "input", "event": "press"})
            assert await fresh.receive_json() == {"type": "state", "state": "listening"}


async def test_a_card_shown_from_a_turn_reaches_every_client_and_carries_spoken(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, cards, _, _ = _cards_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/ws") as other:
            await _connect(ws)
            await _connect(other)
            seen = await _turn_collecting_cards(ws, session)
            seen_by_other = await other.receive_json()
            while seen_by_other["type"] != "card":
                seen_by_other = await other.receive_json()
    assert len(seen) == 1
    card = seen[0]["card"]
    assert card["kind"] == "confirm"
    assert card["id"] == session.shown[0]
    assert card["title"] == card["spoken"] == "Call Priya, your daughter?"
    assert seen_by_other == seen[0]
    assert session.spoken == [card["spoken"]]  # the engine said what the screen shows


async def test_a_tap_answers_the_card_clears_it_everywhere_and_reaches_on_answer(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, cards, _, _ = _cards_app()
    answers = []
    cards.on_answer(answers.append)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/ws") as other:
            await _connect(ws)
            await _connect(other)
            seen = await _turn_collecting_cards(ws, session)
            card_id = seen[0]["card"]["id"]
            # drain the other client's copy of the whole turn
            while (await other.receive_json()).get("state") != "idle":
                pass

            await other.send_json({"type": "card_answer", "id": "stale", "answer": {"yes": True}})
            await other.send_json({"type": "card_answer", "id": card_id, "answer": {"yes": True}})
            assert await ws.receive_json() == {"type": "card", "card": None}
            assert await other.receive_json() == {"type": "card", "card": None}
    assert len(answers) == 1
    assert answers[0].card_id == card_id
    assert answers[0].yes is True
    assert answers[0].source == "tap"
    assert cards.current is None


async def test_a_voice_answer_goes_through_the_same_door_as_a_tap(monkeypatch):
    # A tool, on the executor thread, calls cards.answer(..., source="voice")
    # when she says "yes"; the screen clears the same way.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, cards, _, _ = _cards_app()
    answers = []
    cards.on_answer(answers.append)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            seen = await _turn_collecting_cards(ws, session)
            card_id = seen[0]["card"]["id"]
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(
                None, lambda: cards.answer(card_id, {"yes": False}, source="voice")
            )
            assert ok is True
            assert await ws.receive_json() == {"type": "card", "card": None}
    assert [(a.yes, a.source) for a in answers] == [(False, "voice")]


async def test_malformed_card_answers_are_dropped_and_the_connection_lives(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, cards, _, _ = _cards_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            seen = await _turn_collecting_cards(ws, session)
            card_id = seen[0]["card"]["id"]
            await ws.send_json({"type": "card_answer", "id": 7, "answer": {"yes": True}})
            await ws.send_json({"type": "card_answer", "id": card_id, "answer": "yes"})
            await ws.send_json({"type": "card_answer", "id": card_id})
            await ws.send_json({"type": "card_answer", "id": card_id, "answer": {"dismiss": True}})
            assert await ws.receive_json() == {"type": "card", "card": None}
    assert cards.current is None


# -- the hold seam ---------------------------------------------------------


async def test_a_short_press_while_a_hold_handler_is_set_does_nothing(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    fired = []
    app, session, cards, hold, core = _cards_app(
        hold_seconds=0.5, on_hold_complete=lambda: fired.append(None)
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            first = await ws.receive_json()  # the holding card at 0
            assert first["type"] == "card" and first["card"]["kind"] == "holding"
            assert first["card"]["progress"] == 0.0
            assert first["card"]["title"] == "Hold"
            await asyncio.sleep(0.15)
            await ws.send_json({"type": "input", "event": "release"})
            # Whatever progress ticks arrived, the last thing is the clear.
            message = await ws.receive_json()
            while message != {"type": "card", "card": None}:
                assert message["type"] == "card" and message["card"]["progress"] < 1.0
                message = await ws.receive_json()
            await asyncio.sleep(0.6)  # well past the threshold: nothing fires
    assert fired == []
    assert core.state == State.SLEEPING  # core.py never heard the press
    assert session.start_calls == 0
    assert FakeCapture.instances == []
    assert cards.current is None


async def test_a_press_held_past_the_threshold_fires_once_with_progress_on_the_way(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    fired = []
    app, session, cards, hold, core = _cards_app(
        hold_seconds=0.35, on_hold_complete=lambda: fired.append(None)
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            progress = []
            message = await ws.receive_json()
            while message != {"type": "card", "card": None}:
                assert message["type"] == "card"
                progress.append(message["card"]["progress"])
                message = await ws.receive_json()
            assert fired == [None]
            assert progress[0] == 0.0
            assert progress == sorted(progress)  # monotonic
            assert 0.0 < progress[-1] < 1.0  # completion clears rather than showing 1.0
            # Still holding: nothing more happens, and the release is quiet.
            await asyncio.sleep(0.2)
            await ws.send_json({"type": "input", "event": "release"})
            await asyncio.sleep(0.1)
            # A second, separate hold fires a second time -- once each.
            await ws.send_json({"type": "input", "event": "press"})
            message = await ws.receive_json()
            while message != {"type": "card", "card": None}:
                message = await ws.receive_json()
            assert fired == [None, None]
            await ws.send_json({"type": "input", "event": "release"})
    assert core.state == State.SLEEPING
    assert session.start_calls == 0


async def test_a_handler_set_while_the_key_is_down_does_not_swallow_the_release(monkeypatch):
    # Found in review: the press went to core.py, a call connected and
    # set the hold handler, and the release then hit the hold branch --
    # core stuck in LISTENING with the capture running forever.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    app, session, cards, hold, core = _cards_app(hold_seconds=1.0)
    hold.clear()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            hold.set_handler(lambda: None, seconds=1.0)  # a call connects mid-press
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            # (the harness's fake turn shows a card of its own on the way)
            states = []
            while "idle" not in states:
                message = await ws.receive_json()
                if message["type"] == "state":
                    states.append(message["state"])
            assert states == ["speaking", "idle"]
            # And the next press is a hold, as the handler asked.
            await ws.send_json({"type": "input", "event": "press"})
            first = await ws.receive_json()
            assert first["type"] == "card" and first["card"]["kind"] == "holding"
            await ws.send_json({"type": "input", "event": "release"})
    assert core.state == State.IDLE
    assert FakeCapture.instances[0].stopped is True


async def test_clearing_the_handler_mid_hold_ends_the_timer_and_the_next_hold_still_works(
    monkeypatch,
):
    # Found in review: hold.clear() during a press left the tick task
    # running forever and every later hold press was ignored.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    fired = []
    app, session, cards, hold, core = _cards_app(
        hold_seconds=0.3, on_hold_complete=lambda: fired.append(None)
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            assert (await ws.receive_json())["card"]["kind"] == "holding"
            hold.clear()  # the other party hung up first
            message = await ws.receive_json()
            while message != {"type": "card", "card": None}:
                message = await ws.receive_json()
            await asyncio.sleep(0.5)  # past the threshold: nothing fires
            assert fired == []
            await ws.send_json({"type": "input", "event": "release"})
            # core.py never saw that press, so the release goes nowhere.
            await asyncio.sleep(0.05)
            assert core.state == State.SLEEPING

            hold.set_handler(lambda: fired.append(None), seconds=0.2)
            await ws.send_json({"type": "input", "event": "press"})
            message = await ws.receive_json()
            assert message["card"]["kind"] == "holding"
            while message != {"type": "card", "card": None}:
                message = await ws.receive_json()
            assert fired == [None]
            await ws.send_json({"type": "input", "event": "release"})
    assert session.start_calls == 0


async def test_with_the_hold_handler_cleared_the_spacebar_is_a_spacebar_again(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    app, session, cards, hold, core = _cards_app(hold_seconds=1.0)
    hold.clear()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
    assert core.state == State.LISTENING
    assert session.start_calls == 1


# -- a tap answering a card ends the exchange (2026-09-26) -------------------
#
# Found in a real headless Chromium: the search turn showed the card while
# THINKING and then read the three titles out; a tap mid-reading cleared
# the card and started the video, and the reading carried on. Three
# behaviours pinned here: a card stays until it is answered or dismissed
# (a reload gets it back), an answer always lands whatever state the turn
# is in, and answering ends the exchange.


def _slow_cards_app(show_card=True, block_end_turn=False):
    """A session whose reply is a card's spoken text, and whose say()
    blocks until interrupt() (or a release event), so the tap can land
    while SPEAKING; with block_end_turn, end_turn() blocks after showing
    the card so the tap can land while THINKING."""
    from saathi.screen.cards import CardController, confirm

    cards = CardController()
    say_started = threading.Event()
    say_release = threading.Event()
    reply_release = threading.Event()

    class SlowCardSession(FakeSession):
        shown: list[str] = []

        def end_turn(self) -> str:
            if not show_card:
                return "an ordinary reply"
            card = confirm("Call Priya, your daughter?")
            SlowCardSession.shown.append(cards.show(card))
            if block_end_turn:
                reply_release.wait(timeout=2.0)
            return card.spoken

        def say(self, text: str) -> None:
            self.spoken.append(text)
            say_started.set()
            say_release.wait(timeout=2.0)
            say_started.clear()

        def interrupt(self) -> None:
            super().interrupt()
            say_release.set()

    session = SlowCardSession()
    SlowCardSession.shown = []
    core = Core()
    app = build_app(core, session=session, capture_source_id="fake-aec-source", cards=cards)
    return app, session, cards, core, say_started, say_release, reply_release


async def _start_turn(ws) -> None:
    await ws.send_json({"type": "input", "event": "press"})
    assert await ws.receive_json() == {"type": "state", "state": "listening"}
    await ws.send_json({"type": "input", "event": "release"})
    assert await ws.receive_json() == {"type": "state", "state": "thinking"}


async def test_a_tap_while_the_options_are_being_read_stops_the_reading_and_ends_the_turn(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, cards, core, say_started, _, _ = _slow_cards_app()
    answers = []
    cards.on_answer(answers.append)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _start_turn(ws)
            card = await ws.receive_json()
            assert card["type"] == "card"
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            loop = asyncio.get_running_loop()
            assert await loop.run_in_executor(None, say_started.wait, 2.0)

            await ws.send_json(
                {"type": "card_answer", "id": card["card"]["id"], "answer": {"yes": True}}
            )
            assert await ws.receive_json() == {"type": "card", "card": None}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert session.interrupt_calls == 1  # the reading was cut short...
    assert [(a.yes, a.source) for a in answers] == [(True, "tap")]  # ...and the answer landed
    assert cards.current is None
    assert core.state == State.IDLE


async def test_a_tap_before_the_reply_is_spoken_ends_the_turn_with_nothing_said(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, cards, core, _, say_release, reply_release = _slow_cards_app(
        block_end_turn=True
    )
    answers = []
    cards.on_answer(answers.append)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _start_turn(ws)
            card = await ws.receive_json()
            assert card["type"] == "card"
            assert core.state == State.THINKING  # the model is still composing the question

            await ws.send_json(
                {"type": "card_answer", "id": card["card"]["id"], "answer": {"yes": False}}
            )
            assert await ws.receive_json() == {"type": "card", "card": None}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
            reply_release.set()  # the model's reply arrives late...
            say_release.set()
            await asyncio.sleep(0.2)
            # ...and is never spoken: the turn was over when she answered.
            assert session.spoken == []
            assert core.state == State.IDLE
            # The spacebar is a spacebar again.
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
    assert [(a.yes, a.source) for a in answers] == [(False, "tap")]


async def test_a_tap_on_a_card_from_an_earlier_turn_does_not_end_the_live_one(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    from saathi.screen.cards import confirm

    app, session, cards, core, say_started, say_release, _ = _slow_cards_app(show_card=False)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            # A card shown between turns (initiative, a call's own loop).
            loop = asyncio.get_running_loop()
            card = confirm("Take your tablets?")
            await loop.run_in_executor(None, cards.show, card)
            assert (await ws.receive_json())["card"]["id"] == card.id
            # An unrelated turn is now speaking.
            await _start_turn(ws)
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await loop.run_in_executor(None, say_started.wait, 2.0)

            await ws.send_json({"type": "card_answer", "id": card.id, "answer": {"yes": True}})
            assert await ws.receive_json() == {"type": "card", "card": None}
            assert core.state == State.SPEAKING  # her answer is not about this reply
            say_release.set()
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert session.interrupt_calls == 0


async def test_a_tap_on_the_media_card_lands_and_plays_whatever_state_the_turn_is_in(
    monkeypatch,
):
    # "An answer always lands": the search turn is still THINKING (the
    # model composing the offer) when she taps the second one.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    from saathi.screen.cards import CardController
    from saathi.tools.media import MediaController, make_media_tool
    from saathi.tools.registry import Registry

    found = _media_fixture_results()
    cards = CardController()
    controller = MediaController(search=lambda _q: found, cards=cards)
    registry = Registry()
    registry.register(make_media_tool(controller))
    reply_release = threading.Event()

    class SlowToolSession(ToolSession):
        def end_turn(self) -> str:
            reply = super().end_turn()
            reply_release.wait(timeout=2.0)
            return reply

    session = SlowToolSession(
        lambda name, args: registry.call(name, frozenset({"music"}), **args)
    )
    app = build_app(
        Core(), session=session, capture_source_id="fake", media=controller, cards=cards
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            session.queue(action="search", query="old Chinese songs")
            await _start_turn(ws)
            card = (await ws.receive_json())["card"]
            await ws.send_json({"type": "card_answer", "id": card["id"], "answer": {"choice": 2}})
            assert await ws.receive_json() == {"type": "card", "card": None}
            play = await ws.receive_json()
            assert play["type"] == "media" and play["action"] == "play"
            assert play["video_id"] == found[1].video_id
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
            reply_release.set()
            await asyncio.sleep(0.2)
            assert session.spoken == []  # the offer is never read: she already chose
    assert controller.now_playing == found[1]


async def test_a_stale_tap_is_dropped_and_said_in_the_log(monkeypatch, caplog):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    caplog.set_level(logging.INFO, logger="saathi.screen.server")
    app, session, cards, _, _ = _cards_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            seen = await _turn_collecting_cards(ws, session)
            card_id = seen[0]["card"]["id"]
            await ws.send_json({"type": "card_answer", "id": "gone", "answer": {"yes": True}})
            await ws.send_json({"type": "card_answer", "id": card_id, "answer": {"yes": True}})
            assert await ws.receive_json() == {"type": "card", "card": None}
    assert "gone" in caplog.text and "not the current card" in caplog.text


async def test_a_player_error_is_logged_with_its_code_and_reaches_the_controller(
    monkeypatch, caplog
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, found = _media_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await _turn_collecting_media(ws, session, action="search", query="q")
            await _turn_collecting_media(ws, session, action="play", choice=1)
            await ws.send_json(
                {
                    "type": "media_event",
                    "event": "error",
                    "video_id": found[0].video_id,
                    "code": 150,
                }
            )
            await asyncio.sleep(0.1)
    assert found[0].video_id in controller.unplayable
    assert found[0].video_id in caplog.text and "code 150" in caplog.text


async def test_a_second_client_gets_the_browser_target_play_and_its_browser_error_lands(
    monkeypatch, caplog
):
    # The Android shell is just another /ws client: it sees the play the
    # face page's panel ignores, and its report goes through the same
    # door as the panel's. The server tells the two apart by nothing.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, found = _media_app()
    watch = f"https://www.youtube.com/watch?v={found[0].video_id}"
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as face, client.ws_connect("/ws") as shell:
            await _connect(face)
            await _connect(shell)
            await _turn_collecting_media(face, session, action="search", query="q")
            (play,) = await _turn_collecting_media(face, session, action="play", choice=1)
            assert play["target"] == "embed" and play["watch_url"] == watch
            # The face page's embed refuses it: the controller re-sends it
            # on the browser target, to every client.
            await face.send_json(
                {
                    "type": "media_event",
                    "event": "error",
                    "video_id": found[0].video_id,
                    "code": 150,
                }
            )
            while True:
                message = await shell.receive_json()
                if message["type"] == "media" and message.get("target") == "browser":
                    break
            assert message["action"] == "play"
            assert message["video_id"] == found[0].video_id and message["watch_url"] == watch
            assert message["volume"] == 70 and message["fullscreen"] is False
            assert await face.receive_json() == message  # the panel ignores it; the socket doesn't
            # The watch page can't play it either: the shell's report refuses it.
            await shell.send_json(
                {
                    "type": "media_event",
                    "event": "error",
                    "video_id": found[0].video_id,
                    "code": "browser",
                }
            )
            await asyncio.sleep(0.1)
    assert found[0].video_id in controller.unplayable
    assert found[0].video_id in controller.refused
    assert controller.playing is False
    assert "code browser" in caplog.text


# -- captions: what was heard and said, only while switched on -------------


class HeardSession(FakeSession):
    def end_turn(self) -> str:
        self.last_heard = "play a song by Ed Sheeran"
        return "Here are three songs."


async def _turn_messages(ws) -> list[dict]:
    await ws.send_json({"type": "input", "event": "press"})
    await ws.receive_json()  # listening
    await ws.send_json({"type": "input", "event": "release"})
    got = []
    while True:
        message = await ws.receive_json()
        got.append(message)
        if message == {"type": "state", "state": "idle"}:
            return got


async def test_captions_are_off_by_default_and_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    store = _tmp_store()
    app = build_app(Core(), session=HeardSession(), capture_source_id="src", store=store)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            first = await ws.receive_json()
            settings = await ws.receive_json()
            assert first["type"] == "state" and settings["captions"] is False
            got = await _turn_messages(ws)
    store.close()
    assert not [m for m in got if m["type"] == "caption"]


async def test_captions_on_sends_heard_then_reply_to_every_screen(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    store = _tmp_store()
    app = build_app(Core(), session=HeardSession(), capture_source_id="src", store=store)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/ws") as other:
            await _connect(ws)
            await _connect(other)
            await ws.send_json({"type": "set_preference", "key": "captions", "value": "on"})
            assert (await ws.receive_json())["ok"] is True
            assert (await ws.receive_json())["captions"] is True
            assert (await other.receive_json())["captions"] is True
            got = await _turn_messages(ws)
            other_got = [await other.receive_json() for _ in range(len(got) + 1)]
    store.close()
    captions = [m for m in got if m["type"] == "caption"]
    assert captions == [
        {"type": "caption", "who": "her", "text": "play a song by Ed Sheeran"},
        {"type": "caption", "who": "saathi", "text": "Here are three songs."},
    ]
    assert [m for m in other_got if m["type"] == "caption"] == captions


async def test_a_captions_value_outside_on_off_is_dropped(monkeypatch):
    store = _tmp_store()
    app = build_app(Core(), store=store)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "set_preference", "key": "captions", "value": "yes"})
            await ws.send_json({"type": "set_preference", "key": "language", "value": "english"})
            reply = await ws.receive_json()
    store.close()
    assert reply["key"] == "language"  # the malformed one produced nothing


# -- /audio: the phone as the microphone and the speaker (2026-10-08) --------


class HearingSession(FakeSession):
    """A FakeSession that keeps what it was fed."""

    def __init__(self) -> None:
        super().__init__()
        self.heard: list[bytes] = []

    def send_audio(self, chunk: bytes) -> None:
        self.heard.append(chunk)


class RemoteSpeakingSession(HearingSession):
    """Speaks the way cascade.py does: one WAV per sentence, through the
    player seam, blocking in the executor thread until the handle says
    the sentence has played; `interrupt()` stops the current handle."""

    def __init__(self, player, wav: bytes) -> None:
        super().__init__()
        self._player = player
        self._wav = wav
        self.handles: list = []

    def say(self, text: str) -> None:
        super().say(text)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            path = Path(tmp_file.name)
        path.write_bytes(self._wav)
        try:
            handle = self._player("fake-sink", path)
            self.handles.append(handle)
            handle.wait()
        finally:
            path.unlink(missing_ok=True)

    def interrupt(self) -> None:
        super().interrupt()
        for handle in self.handles:
            handle.stop()


def _silence_wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


async def _eventually(condition, seconds: float = 2.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.01)


def _audio_app(session, remote=None):
    remote = remote or RemoteAudio()
    core = Core()
    app = build_app(core, session=session, capture_source_id="fake-aec-source", remote_audio=remote)
    return app, remote, core


async def _hello(audio) -> None:
    await audio.send_json({"type": "hello", "client": "android", "sample_rate": 16000})


async def test_an_audio_clients_frames_reach_the_session_only_between_press_and_release(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    app, remote, core = _audio_app(session)
    frame = b"\x01\x00" * 1600  # 100 ms of 16 kHz PCM16
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            assert await _connect(ws) == {"type": "state", "state": "sleeping"}
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            # The client may send whenever it likes; before the press
            # nobody is listening.
            await audio.send_bytes(frame)
            await asyncio.sleep(0.05)
            assert session.heard == []

            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await audio.send_bytes(frame)
            await audio.send_bytes(frame)
            await _eventually(lambda: len(session.heard) == 2)
            assert session.heard == [frame, frame]

            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            await audio.send_bytes(frame)
            await asyncio.sleep(0.05)
            assert len(session.heard) == 2  # the release closed the mic
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert session.start_calls == 1
    assert FakeCapture.instances == []  # the phone was the microphone; parec never ran


async def test_with_an_audio_client_attached_a_press_starts_no_capture(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    app, remote, core = _audio_app(session)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            assert FakeCapture.instances == []
            assert session.start_calls == 1
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert FakeCapture.instances == []


async def test_tts_from_a_real_turn_reaches_the_audio_client_and_its_played_ack_ends_the_turn(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    wav = _silence_wav(0.05)
    remote = RemoteAudio()
    session = RemoteSpeakingSession(remote.player, wav)
    app, remote, core = _audio_app(session, remote)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}

            header = await audio.receive_json()
            assert header == {"type": "play", "id": header["id"], "format": "wav"}
            body = await audio.receive()
            assert body.type == server_module.WSMsgType.BINARY and body.data == wav
            # Not over until the phone says so: the WAV is 50 ms, the
            # grace is 3 s, and nothing has arrived after 200 ms.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.receive_json(), 0.2)
            assert core.state == State.SPEAKING

            await audio.send_json({"type": "played", "id": header["id"]})
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert session.spoken == ["reply"]
    assert session.handles[0].acknowledged
    assert FakeCapture.instances == []


async def test_a_barge_in_sends_stop_to_the_audio_client(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    remote = RemoteAudio()
    session = RemoteSpeakingSession(remote.player, _silence_wav(0.05))
    app, remote, core = _audio_app(session, remote)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            header = await audio.receive_json()
            await audio.receive()  # the WAV
            # She talks over it: the phone is told to stop, and listens again.
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            assert await audio.receive_json() == {"type": "stop"}
            await audio.send_json({"type": "played", "id": header["id"]})  # what it cut short
            await asyncio.sleep(0.05)
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            second = await audio.receive_json()
            assert second["type"] == "play" and second["id"] != header["id"]
            await audio.receive()
            await audio.send_json({"type": "played", "id": second["id"]})
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert session.interrupt_calls == 1
    assert FakeCapture.instances == []


async def test_detaching_the_audio_client_restores_the_parec_path(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    app, remote, core = _audio_app(session)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            async with client.ws_connect("/audio") as audio:
                await _hello(audio)
                await _eventually(lambda: remote.attached)
            await _eventually(lambda: not remote.attached)

            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            assert len(FakeCapture.instances) == 1
            assert FakeCapture.instances[0].source_id == "fake-aec-source"
            assert FakeCapture.instances[0].started is True
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert FakeCapture.instances[0].stopped is True


async def test_a_second_audio_client_replaces_the_first(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    app, remote, core = _audio_app(session)
    frame = b"\x02\x00" * 1600
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as first:
            await _connect(ws)
            await _hello(first)
            await _eventually(lambda: remote.attached)
            async with client.ws_connect("/audio") as second:
                await _hello(second)
                # The server closes the replaced connection itself.
                closing = await first.receive()
                assert closing.type in (
                    server_module.WSMsgType.CLOSE,
                    server_module.WSMsgType.CLOSING,
                    server_module.WSMsgType.CLOSED,
                )
                await _eventually(lambda: remote.attached)
                await ws.send_json({"type": "input", "event": "press"})
                assert await ws.receive_json() == {"type": "state", "state": "listening"}
                await second.send_bytes(frame)
                await _eventually(lambda: session.heard == [frame])
                await ws.send_json({"type": "input", "event": "release"})
                assert await ws.receive_json() == {"type": "state", "state": "thinking"}
                assert await ws.receive_json() == {"type": "state", "state": "speaking"}
                assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert FakeCapture.instances == []


async def test_without_a_remote_audio_there_is_no_audio_route():
    async with TestClient(TestServer(build_app(Core()))) as client:
        response = await client.get("/audio")
        assert response.status == 404


async def test_with_no_local_mic_a_phone_still_holds_a_turn(monkeypatch):
    # A device with no usable local capture source (a mic-less laptop,
    # a Pi whose USB mic is unplugged) still runs a turn through the
    # phone: the /audio client is the microphone.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    remote = RemoteAudio()
    core = Core()
    app = build_app(core, session=session, capture_source_id=None, remote_audio=remote)
    frame = b"\x03\x00" * 1600
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await audio.send_bytes(frame)
            await _eventually(lambda: session.heard == [frame])
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert session.start_calls == 1
    assert FakeCapture.instances == []


async def test_with_no_local_mic_and_no_phone_a_press_starts_nothing(monkeypatch):
    # The pre-existing rule the `or remote_mic` must not widen: with no
    # capture source and no /audio client, a press starts no session and
    # no capture.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    core = Core()
    app = build_app(core, session=session, capture_source_id=None, remote_audio=RemoteAudio())
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await asyncio.sleep(0.05)
            assert session.start_calls == 0
            assert FakeCapture.instances == []
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
    assert session.start_calls == 0
    assert FakeCapture.instances == []


async def test_a_client_that_never_acks_does_not_hold_the_turn(monkeypatch):
    # "The engine must never hang on a dead client", seen through /ws: a
    # client that takes the WAV and never says `played` still lets the
    # state machine reach idle, at the WAV's length plus the grace.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    remote = RemoteAudio(grace_seconds=0.1)
    session = RemoteSpeakingSession(remote.player, _silence_wav(0.05))
    app, remote, core = _audio_app(session, remote)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert (await audio.receive_json())["type"] == "play"
            await audio.receive()  # the WAV; no `played` ever follows
            assert await asyncio.wait_for(ws.receive_json(), 1.0) == {
                "type": "state",
                "state": "idle",
            }
            assert remote.attached  # slow is not dead: the client stays
    assert not session.handles[0].acknowledged
    assert session.spoken == ["reply"]


async def test_bad_audio_text_frames_are_logged_and_the_client_stays_attached(
    monkeypatch, caplog
):
    # A bad frame must not detach the mic mid-hold: each is dropped with
    # a log line and the socket lives on.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    app, remote, core = _audio_app(session)
    frame = b"\x04\x00" * 1600
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await audio.send_json({"type": "hello", "client": "android", "sample_rate": 44100})
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await audio.send_str("not json")
            await audio.send_json({"type": "bogus"})
            await audio.send_json({"type": "played", "id": 7})
            await audio.send_bytes(frame)
            await _eventually(lambda: session.heard == [frame])
            assert remote.attached
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
            assert remote.attached
    assert "sample_rate 44100 is not 16000" in caplog.text
    assert "dropped malformed audio message" in caplog.text
    assert "dropped unknown audio message" in caplog.text
    assert FakeCapture.instances == []


async def _dropped_by_the_server(ws) -> None:
    """Read a quiet client's socket (autoping off) until the server's
    close reaches it; the unanswered pings and the broadcasts sent before
    the drop sit in its queue first. Used instead of `ws.close()`, which
    raises on a transport the server has already torn down."""
    closing = (
        server_module.WSMsgType.CLOSE,
        server_module.WSMsgType.CLOSING,
        server_module.WSMsgType.CLOSED,
        server_module.WSMsgType.ERROR,
    )
    while True:
        message = await asyncio.wait_for(ws.receive(), 3.0)
        if message.type in closing:
            return


async def _reading_until(ws, condition, seconds: float = 3.0) -> None:
    """Wait for `condition` while reading `ws`, so that socket keeps
    answering the server's pings (aiohttp's client only pongs inside
    `receive()`). Nothing it reads is expected."""
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        try:
            message = await asyncio.wait_for(ws.receive(), 0.05)
        except asyncio.TimeoutError:
            continue
        raise AssertionError(f"unexpected {message}")


async def test_an_audio_client_that_stops_answering_pings_is_detached(monkeypatch):
    # A phone off Wi-Fi sends no close frame. The server's heartbeat is
    # what notices: a missed pong closes the socket, the handler detaches
    # the client, and the local mic and speaker are back.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    monkeypatch.setattr(server_module, "_HEARTBEAT_SECONDS", 0.2)
    FakeCapture.instances.clear()
    session = HearingSession()
    app, remote, core = _audio_app(session)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            audio = await client.ws_connect("/audio", autoping=False)  # a client gone quiet
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await _reading_until(ws, lambda: not remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            assert len(FakeCapture.instances) == 1  # parec again
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
            await _dropped_by_the_server(audio)


async def test_a_ws_client_that_vanishes_with_its_press_down_is_released(monkeypatch):
    # The shell's socket dies mid-hold (a Wi-Fi hiccup; here, a client
    # that stops answering pings): the release it would have sent is
    # lost with it, and the shell never re-sends one. The heartbeat
    # notices, the server releases the press on the way out, the turn
    # runs, and the face does not sit on listening until her next hold.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    monkeypatch.setattr(server_module, "_HEARTBEAT_SECONDS", 0.2)
    FakeCapture.instances.clear()
    session = HearingSession()
    core = Core()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as watcher:
            await _connect(watcher)
            holder = await client.ws_connect("/ws", autoping=False)
            await _connect(holder)
            await holder.send_json({"type": "input", "event": "press"})
            assert await watcher.receive_json() == {"type": "state", "state": "listening"}
            assert FakeCapture.instances[0].started is True
            # Nothing more from the holder: its socket goes with the key down.
            assert await asyncio.wait_for(watcher.receive_json(), 3.0) == {
                "type": "state",
                "state": "thinking",
            }
            assert await watcher.receive_json() == {"type": "state", "state": "speaking"}
            assert await watcher.receive_json() == {"type": "state", "state": "idle"}
            assert FakeCapture.instances[0].stopped is True
            await _dropped_by_the_server(holder)
            # Another client's press afterwards is a fresh hold, not a no-op.
            await watcher.send_json({"type": "input", "event": "press"})
            assert await watcher.receive_json() == {"type": "state", "state": "listening"}
            await watcher.send_json({"type": "input", "event": "release"})
            assert await watcher.receive_json() == {"type": "state", "state": "thinking"}
            assert await watcher.receive_json() == {"type": "state", "state": "speaking"}
            assert await watcher.receive_json() == {"type": "state", "state": "idle"}
    assert session.start_calls == 2
    assert len(FakeCapture.instances) == 2


async def test_a_ws_client_that_vanishes_without_a_press_down_releases_nothing(monkeypatch):
    # Only the socket whose press is down is released: the face page
    # dropping while the shell holds the button must not end her turn.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    monkeypatch.setattr(server_module, "_HEARTBEAT_SECONDS", 0.2)
    FakeCapture.instances.clear()
    session = HearingSession()
    core = Core()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as holder:
            await _connect(holder)
            bystander = await client.ws_connect("/ws", autoping=False)
            await _connect(bystander)
            await holder.send_json({"type": "input", "event": "press"})
            assert await holder.receive_json() == {"type": "state", "state": "listening"}
            # The bystander is long dropped by then; the holder, reading
            # (and so answering pings), hears no state change at all.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(holder.receive_json(), 0.6)
            await _dropped_by_the_server(bystander)
            assert core.state == State.LISTENING
            assert FakeCapture.instances[0].stopped is False
            await holder.send_json({"type": "input", "event": "release"})
            assert await holder.receive_json() == {"type": "state", "state": "thinking"}
            assert await holder.receive_json() == {"type": "state", "state": "speaking"}
            assert await holder.receive_json() == {"type": "state", "state": "idle"}
    assert session.start_calls == 1


async def test_a_ws_client_that_closes_in_order_with_its_press_down_is_taken_at_its_word(
    monkeypatch,
):
    # A normal close (1000) is a client that had its chance to release:
    # the shell always releases before it disconnects, and the press
    # stands -- the rule every earlier test that closes mid-press relies
    # on. Only a vanished client is released for.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    session = HearingSession()
    core = Core()
    app = build_app(core, session=session, capture_source_id="fake-aec-source")
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
        await asyncio.sleep(0.1)
    assert core.state == State.LISTENING
    assert FakeCapture.instances[0].stopped is False


async def test_a_hold_socket_that_vanishes_mid_hold_abandons_the_hold(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    monkeypatch.setattr(server_module, "_HEARTBEAT_SECONDS", 0.2)
    FakeCapture.instances.clear()
    fired = []
    app, session, cards, hold, core = _cards_app(
        hold_seconds=1.0, on_hold_complete=lambda: fired.append(None)
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as watcher:
            await _connect(watcher)
            holder = await client.ws_connect("/ws", autoping=False)
            await _connect(holder)
            await holder.send_json({"type": "input", "event": "press"})
            first = await watcher.receive_json()  # the holding card at 0
            assert first["type"] == "card" and first["card"]["kind"] == "holding"
            # The socket goes with the key down: the hold is abandoned,
            # the card cleared, and the handler never fires.
            message = await watcher.receive_json()
            while message != {"type": "card", "card": None}:
                assert message["type"] == "card" and message["card"]["progress"] < 1.0
                message = await asyncio.wait_for(watcher.receive_json(), 3.0)
            await _dropped_by_the_server(holder)
            with pytest.raises(asyncio.TimeoutError):  # well past the threshold: no card, no fire
                await asyncio.wait_for(watcher.receive_json(), 0.8)
    assert fired == []
    assert not hold.holding
    assert core.state == State.SLEEPING
    assert cards.current is None


async def test_a_reset_names_its_player_and_the_server_passes_it_through(monkeypatch):
    # The face page's `reset` (no target) on a reconnect while the shell
    # plays the browser target changes nothing; the shell's own
    # (`target: "browser"`) is the one that un-plays it.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    app, session, controller, found = _media_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as face, client.ws_connect("/ws") as shell:
            await _connect(face)
            await _connect(shell)
            await _turn_collecting_media(face, session, action="search", query="q")
            await _turn_collecting_media(face, session, action="play", choice=1)
            await face.send_json(
                {
                    "type": "media_event",
                    "event": "error",
                    "video_id": found[0].video_id,
                    "code": 150,
                }
            )
            await _eventually(lambda: controller.target == "browser")
            await face.send_json({"type": "media_event", "event": "reset"})
            await face.send_json({"type": "media_event", "event": "reset", "target": "embed"})
            await shell.send_json({"type": "media_event", "event": "reset", "target": 3})
            await asyncio.sleep(0.1)
            assert controller.playing is True
            await shell.send_json({"type": "media_event", "event": "reset", "target": "browser"})
            await _eventually(lambda: controller.playing is False)


async def test_without_an_audio_client_an_embed_refusal_is_reoffered_not_sent_to_the_browser(
    monkeypatch,
):
    # cli.py's wiring: the browser target has a taker only while the
    # shell's /audio socket is attached. With none (the Pi kiosk, a
    # laptop demo), the embed's refusal refuses the video as it used to.
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    from saathi.tools.media import MediaController, make_media_tool
    from saathi.tools.registry import Registry

    found = _media_fixture_results()
    remote = RemoteAudio()
    controller = MediaController(
        search=lambda _query: found, browser_available=lambda: remote.attached
    )
    registry = Registry()
    registry.register(make_media_tool(controller))
    session = ToolSession(
        lambda name, arguments: registry.call(name, frozenset({"music"}), **arguments)
    )
    app = build_app(
        Core(),
        session=session,
        capture_source_id="fake-aec-source",
        media=controller,
        remote_audio=remote,
    )
    refusal = {"type": "media_event", "event": "error", "video_id": found[0].video_id, "code": 150}
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as face:
            await _connect(face)
            await _turn_collecting_media(face, session, action="search", query="q")
            await _turn_collecting_media(face, session, action="play", choice=1)
            await face.send_json(refusal)
            await _eventually(lambda: found[0].video_id in controller.refused)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(face.receive_json(), 0.2)  # no browser play
            assert controller.playing is False
            # With the phone attached, the same refusal goes to its pane.
            async with client.ws_connect("/audio") as audio:
                await _hello(audio)
                await _eventually(lambda: remote.attached)
                await _turn_collecting_media(face, session, action="play", choice=2)
                await face.send_json({**refusal, "video_id": found[1].video_id})
                message = await asyncio.wait_for(face.receive_json(), 2.0)
                assert message["action"] == "play" and message["target"] == "browser"
                assert message["video_id"] == found[1].video_id
    assert found[1].video_id in controller.unplayable
    assert found[1].video_id not in controller.refused


# -- /audio: the phone's own voice (2026-10-08, the engine inside the phone) --


class PhoneVoiceSession(HearingSession):
    """Speaks the way voice/tts/remote_backend.py does under cascade.py:
    asks the phone to render the sentence, then plays the WAV it got
    back through the same client. A failed request is recorded, not
    raised, as the backend turns it into silence."""

    def __init__(self, remote) -> None:
        super().__init__()
        self._remote = remote
        self.rendered: list[bytes] = []
        self.failures: list[str] = []

    def say(self, text: str) -> None:
        from saathi.audio.remote import RemoteSynthesisError

        super().say(text)
        try:
            wav = self._remote.synthesize(text, "english", 2.0)
        except RemoteSynthesisError as exc:
            self.failures.append(str(exc))
            return
        self.rendered.append(wav)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            path = Path(tmp_file.name)
        path.write_bytes(wav)
        try:
            self._remote.player("remote", path).wait()
        finally:
            path.unlink(missing_ok=True)


def _phone_app(remote):
    """Remote mode as cli.py builds it: no capture source at all."""
    core = Core()
    session = PhoneVoiceSession(remote)
    app = build_app(core, session=session, capture_source_id=None, remote_audio=remote)
    return app, session, core


async def test_the_synthesized_route_hands_the_next_binary_frame_to_the_waiting_synthesis(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    remote = RemoteAudio()
    app, session, core = _phone_app(remote)
    wav = _silence_wav(0.05)
    frame = b"\x05\x00" * 1600
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await audio.send_bytes(frame)
            await _eventually(lambda: session.heard == [frame])
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}

            # The engine asks the phone for the sentence...
            request = await audio.receive_json()
            assert request == {
                "type": "synthesize",
                "id": request["id"],
                "text": "reply",
                "language": "english",
            }
            # ...the phone answers with the text frame, then the WAV...
            await audio.send_json({"type": "synthesized", "id": request["id"]})
            await audio.send_bytes(wav)
            # ...and is then asked to play exactly that WAV.
            header = await audio.receive_json()
            assert header["type"] == "play"
            body = await audio.receive()
            assert body.type == server_module.WSMsgType.BINARY and body.data == wav
            await audio.send_json({"type": "played", "id": header["id"]})
            assert await ws.receive_json() == {"type": "state", "state": "idle"}
    assert session.rendered == [wav]
    assert session.heard == [frame]  # the WAV never reached the microphone path
    assert session.failures == []
    assert FakeCapture.instances == []


async def test_a_synthesized_error_is_routed_and_a_bad_one_is_dropped(monkeypatch, caplog):
    monkeypatch.setattr(server_module, "Capture", FakeCapture)
    FakeCapture.instances.clear()
    remote = RemoteAudio()
    app, session, core = _phone_app(remote)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/audio") as audio:
            await _connect(ws)
            await _hello(audio)
            await _eventually(lambda: remote.attached)
            await ws.send_json({"type": "input", "event": "press"})
            assert await ws.receive_json() == {"type": "state", "state": "listening"}
            await ws.send_json({"type": "input", "event": "release"})
            assert await ws.receive_json() == {"type": "state", "state": "thinking"}
            assert await ws.receive_json() == {"type": "state", "state": "speaking"}
            request = await audio.receive_json()
            assert request["type"] == "synthesize"
            # A malformed answer is dropped with a log line, the socket lives on...
            await audio.send_json({"type": "synthesized", "id": 7})
            # ...and the real answer, an error, reaches the waiting synthesis.
            await audio.send_json(
                {"type": "synthesized", "id": request["id"], "error": "no voice for english"}
            )
            assert await asyncio.wait_for(ws.receive_json(), 1.0) == {
                "type": "state",
                "state": "idle",
            }
            assert remote.attached
    assert session.rendered == []
    assert session.failures == [
        f"the client could not synthesize {request['id']}: no voice for english"
    ]
    assert "dropped unknown audio message" not in caplog.text  # it was a known type
