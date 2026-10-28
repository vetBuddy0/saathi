"""The face's emotion door (screen/emotion.py), the phone panel
(screen/call_panel.py), the controller feeding it, and the End call tap
going through the hold seam -- never a second hang-up path."""

import asyncio
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from saathi.call.controller import CallController
from saathi.call.hangup import HANGUP_HOLD_SECONDS, HANGUP_LABEL
from saathi.call.relay import FakeRelay
from saathi.call.twilio import FakeTwilioClient, TwilioCredentials
from saathi.core import Core
from saathi.screen.call_panel import CallPanel, CallView
from saathi.screen.cards import CardController, HoldController
from saathi.screen.emotion import EMOTIONS, MAX_SECONDS, MIN_SECONDS, EmotionController
from saathi.screen.server import build_app

CREDS = TwilioCredentials(
    account_sid="AC" + "0" * 32,
    api_key="SK" + "1" * 32,
    api_secret="s",
    from_number="+1" + "0" * 10,
    test_number="+1" + "0" * 9 + "1",
)


async def _drain_connect(ws) -> None:
    assert (await ws.receive_json())["type"] == "state"
    assert (await ws.receive_json())["type"] == "settings"


# -- EmotionController ----------------------------------------------------


def test_an_allowed_emotion_is_broadcast_with_a_duration():
    sent = []
    emotions = EmotionController(sent.append)
    assert emotions.show("blush") is True
    assert sent == [{"type": "emotion", "emotion": "blush", "seconds": 4.0}]


def test_an_unknown_emotion_is_refused_and_nothing_is_drawn():
    sent = []
    emotions = EmotionController(sent.append)
    assert emotions.show("smug") is False
    assert emotions.show(None) is False  # type: ignore[arg-type]
    assert emotions.show("happy", seconds="soon") is False  # type: ignore[arg-type]
    assert emotions.show("happy", seconds=float("nan")) is False
    assert sent == []


def test_names_are_normalised_and_durations_clamped():
    sent = []
    emotions = EmotionController(sent.append)
    emotions.show("  Surprised ", seconds=0)
    emotions.show("sad", seconds=600)
    assert sent[0]["emotion"] == "surprised" and sent[0]["seconds"] == MIN_SECONDS
    assert sent[1]["seconds"] == MAX_SECONDS


def test_neutral_clears_and_every_allowed_name_is_a_look_the_face_draws():
    sent = []
    emotions = EmotionController(sent.append)
    assert emotions.clear() is True
    assert sent[-1]["emotion"] == "neutral"
    assert {"neutral", "blush", "happy", "sad", "surprised"} <= EMOTIONS


def test_no_screen_attached_is_not_an_error():
    assert EmotionController().show("happy") is True


async def test_an_emotion_shown_off_the_loop_reaches_every_screen():
    emotions = EmotionController()
    app = build_app(Core(), emotions=emotions)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws, client.ws_connect("/ws") as other:
            await _drain_connect(ws)
            await _drain_connect(other)
            # From a worker thread, as a turn's executor would.
            await asyncio.get_running_loop().run_in_executor(None, emotions.show, "blush")
            expected = {"type": "emotion", "emotion": "blush", "seconds": 4.0}
            assert await ws.receive_json() == expected
            assert await other.receive_json() == expected


# -- CallPanel ------------------------------------------------------------


def test_the_panel_message_carries_who_number_status_and_elapsed():
    now = {"t": 100.0}
    sent = []
    panel = CallPanel(sent.append, clock=lambda: now["t"])
    panel.update(CallView("Priya", "+6591234567", "calling"))
    first = sent[-1]["call"]
    assert first["name"] == "Priya" and first["number"] == "+6591234567"
    assert first["status"] == "calling" and first["elapsed_seconds"] is None
    call_id = first["id"]
    panel.update(CallView("Priya", "+6591234567", "connected", connected_at=100.0))
    now["t"] = 165.5
    again = panel.message()["call"]
    assert again["id"] == call_id  # same call, same id
    assert again["status"] == "connected" and again["elapsed_seconds"] == 65.5
    panel.update(None)
    assert sent[-1] == {"type": "call", "call": None}
    assert panel.message() is None and panel.current_id is None


def test_an_unchanged_view_is_not_resent_and_a_new_call_gets_a_new_id():
    sent = []
    panel = CallPanel(sent.append)
    view = CallView("Priya", "+65", "ringing")
    panel.update(view)
    panel.update(view)
    assert len(sent) == 1
    first_id = panel.current_id
    panel.update(None)
    panel.update(None)  # already cleared: nothing more
    assert len(sent) == 2
    panel.update(view)
    assert panel.current_id not in (None, first_id)


def test_an_unknown_status_is_a_bug_not_a_screen():
    with pytest.raises(ValueError):
        CallPanel().update(CallView("x", "1", "on hold"))


# -- CallController -> panel ---------------------------------------------


class _Server:
    def start(self):
        pass

    def stop(self):
        pass


class _Audio:
    def start(self, send):
        pass

    def feed_inbound(self, ulaw):
        pass

    def stop(self):
        pass


def _controller(hold, client=None, **kwargs):
    client = client or FakeTwilioClient()
    controller = CallController(CREDS, client, FakeRelay(), _Server(), _Audio, hold, **kwargs)
    return controller, client


def test_the_controller_reports_calling_ringing_connected_then_gone():
    hold = HoldController(CardController())
    client = FakeTwilioClient()
    client.call_resource = {"status": "ringing"}
    controller, _ = _controller(hold, client, poll_interval_seconds=0.01)
    views = []
    controller.on_view_change = views.append
    controller.dial("+6591234567", who="Priya")
    assert views[0] == CallView("Priya", "+6591234567", "calling")

    end = time.monotonic() + 2
    while not any(v is not None and v.status == "ringing" for v in views):
        assert time.monotonic() < end, "never reported ringing"
        time.sleep(0.01)
    controller.stream_started("MZ1", "CA1", lambda _b: None)
    connected = views[-1]
    assert connected.status == "connected" and connected.connected_at is not None
    controller.hangup(wait=True)
    assert views[-1] is None
    assert [v.status for v in views if v is not None].count("ringing") == 1


def test_the_test_number_is_named_on_the_panel():
    hold = HoldController(CardController())
    controller, _ = _controller(hold)
    views = []
    controller.on_view_change = views.append
    controller.dial_test_number()
    assert views[0].name == "Test call"
    controller.hangup(wait=True)


def test_a_failing_panel_never_breaks_the_call():
    hold = HoldController(CardController())
    controller, client = _controller(hold)

    def broken(_view):
        raise RuntimeError("screen gone")

    controller.on_view_change = broken
    controller.dial("+6591234567", who="Priya")
    controller.hangup(wait=True)
    assert client.completed and not controller.active


# -- HoldController.complete ------------------------------------------------


def test_complete_fires_the_registered_handler_once_and_only_while_registered():
    cards = CardController()
    hold = HoldController(cards)
    assert hold.complete() is False  # nothing registered: nothing happens
    fired = []
    hold.set_handler(lambda: fired.append(1), seconds=2.0, label="Hang up")
    hold.begin()  # a spacebar hold already in progress...
    assert cards.current is not None
    assert hold.complete() is True
    assert fired == [1]
    assert cards.current is None and not hold.holding  # ...and its card goes


# -- the End call tap, end to end -----------------------------------------


def _call_app():
    cards = CardController()
    hold = HoldController(cards)
    panel = CallPanel()
    controller, client = _controller(hold)
    controller.on_view_change = panel.update
    app = build_app(Core(), cards=cards, hold=hold, calls=panel)
    return app, controller, client, panel, hold


async def _next_call_message(ws) -> dict:
    while True:
        message = await ws.receive_json()
        if message["type"] == "call":
            return message


async def test_a_call_shows_the_panel_and_a_reload_gets_it_back():
    app, controller, _client, panel, hold = _call_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _drain_connect(ws)
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: controller.dial("+6591234567", who="Priya")
            )
            shown = await _next_call_message(ws)
            assert shown["call"]["name"] == "Priya"
            assert shown["call"]["status"] == "calling"
            assert hold.active and hold.seconds == HANGUP_HOLD_SECONDS
            assert hold.label == HANGUP_LABEL
        async with client.ws_connect("/ws") as fresh:
            await _drain_connect(fresh)
            assert (await fresh.receive_json())["call"]["id"] == shown["call"]["id"]
        controller.hangup(wait=True)


async def test_end_call_tap_hangs_up_through_the_hold_seam_and_clears_the_panel():
    app, controller, twilio, panel, hold = _call_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _drain_connect(ws)
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: controller.dial("+6591234567", who="Priya")
            )
            shown = await _next_call_message(ws)
            await ws.send_json({"type": "call_hangup", "id": shown["call"]["id"]})
            assert await _next_call_message(ws) == {"type": "call", "call": None}
    for _ in range(100):
        if twilio.completed:
            break
        await asyncio.sleep(0.01)
    assert not controller.active
    assert twilio.completed == [twilio.next_sid]
    assert not hold.active  # the spacebar is a spacebar again


async def test_a_stale_or_malformed_end_call_tap_ends_nothing():
    app, controller, twilio, panel, hold = _call_app()
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _drain_connect(ws)
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: controller.dial("+6591234567", who="Priya")
            )
            await _next_call_message(ws)
            await ws.send_json({"type": "call_hangup", "id": "an-earlier-call"})
            await ws.send_json({"type": "call_hangup"})
            await ws.send_json({"type": "call_hangup", "id": 7})
            await ws.send_json({"type": "input", "event": "nothing"})  # connection lives
            await asyncio.sleep(0.1)
    assert controller.active
    assert twilio.completed == []
    controller.hangup(wait=True)


async def test_an_end_call_tap_with_no_handler_registered_does_nothing():
    cards = CardController()
    hold = HoldController(cards)
    panel = CallPanel()
    panel.update(CallView("Priya", "+65", "calling"))
    app = build_app(Core(), cards=cards, hold=hold, calls=panel)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _drain_connect(ws)
            await ws.receive_json()  # the panel, re-sent on connect
            await ws.send_json({"type": "call_hangup", "id": panel.current_id})
            await asyncio.sleep(0.05)
    assert panel.current_id is not None  # nothing tore it down
