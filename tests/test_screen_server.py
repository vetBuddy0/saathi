import asyncio
import json
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
    ):
        self.stt_ms = stt_ms
        self.first_token_ms = first_token_ms
        self.first_tts_chunk_ms = first_tts_chunk_ms
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd


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


async def test_a_superseded_barge_in_turn_is_not_logged():
    # The turn that got barged into never reaches core.handle(Event("done"))
    # (see _speak_and_finish) -- and _log_turn is called right there, so a
    # superseded turn correctly produces no row at all, not a row with
    # wrong or partial numbers.
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
            # ...and a fresh connection still gets state, settings, nothing
            # else -- no card on connect.
            assert (await _connect(fresh))["type"] == "state"
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
