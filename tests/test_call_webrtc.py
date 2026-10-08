"""call/webrtc.py -- the family-call signaling state machine, headless.

The real card controller, hold controller and phone panel are used (not
fakes): the rules being pinned -- never auto-answered, the End button
and the two-second hold reach the same hang-up, only the two parties of
the current call exchange anything -- live in how those meet."""

from __future__ import annotations

import threading
import time

import pytest

from saathi.call.family import FamilyRegistry
from saathi.call.push import PushError, Subscription
from saathi.call.webrtc import (
    ANSWER_HOLD_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    RING_TIMEOUT_SECONDS,
    FamilyCalls,
    Phase,
)
from saathi.screen.call_panel import CallPanel
from saathi.screen.cards import CardController, HoldController
from saathi.tools.calling import make_answer_card_tool

SUB = Subscription("https://push.test/endpoint", b"\x04" + b"\x01" * 64, b"\x02" * 16)
ICE = [{"urls": ["stun:stun.example.test:3478"]}]


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakePush:
    public_key = "BKEY"

    def __init__(self) -> None:
        self.sent: list[tuple[Subscription, dict]] = []
        self.fail: PushError | None = None
        self.done = threading.Event()

    def send(self, subscription, payload, **_kwargs):
        if self.fail is not None:
            raise self.fail
        self.sent.append((subscription, payload))
        self.done.set()


class Recorder:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def __call__(self, message: dict) -> None:
        self.messages.append(message)

    def of(self, kind_key: str, value: str) -> list[dict]:
        return [m for m in self.messages if m.get(kind_key) == value]


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    member, _ = registry.redeem(registry.issue_pairing_token()[0], "Priya", "daughter", "Mum")
    registry.set_subscription(member.id, SUB)
    member = registry.get(member.id)
    cards = CardController()
    hold = HoldController(cards)
    panel = CallPanel()
    panel_messages = Recorder()
    panel.set_broadcast(panel_messages)
    push = FakePush()
    device = Recorder()
    twilio = {"active": False}
    calls = FamilyCalls(
        registry,
        push=push,
        public_url=lambda: "https://saathi.example.test",
        hold=hold,
        cards=cards,
        panel=panel.update,
        ice_servers=lambda: ICE,
        other_active=lambda: twilio["active"],
        clock=clock,
    )
    calls.set_broadcast(device)
    calls._ensure_ticker = lambda: None  # tests drive tick() with the fake clock
    return {
        "calls": calls,
        "member": member,
        "registry": registry,
        "cards": cards,
        "hold": hold,
        "panel": panel,
        "push": push,
        "device": device,
        "clock": clock,
        "twilio": twilio,
    }


def _phone(world):
    phone = Recorder()
    conn = world["calls"].attach(world["member"], phone)
    return phone, conn


def _connect_out(world):
    """She rings Priya; Priya answers; the kiosk is ready."""
    calls = world["calls"]
    phone, conn = _phone(world)
    result = calls.dial(world["member"])
    assert result["status"] == "calling"
    calls.on_member_message(conn, {"type": "accept", "call_id": calls.call_id})
    calls.on_device_message({"type": "rtc", "action": "ready", "call_id": calls.call_id,
                             "peer": "kiosk"})
    return phone, conn


# -- she calls them -----------------------------------------------------------


def test_dial_rings_the_phone_by_push_with_the_current_url_and_a_call_token(world):
    calls = world["calls"]
    result = calls.dial(world["member"])
    assert result["status"] == "calling" and "Calling Priya." in result["note"]
    assert calls.phase is Phase.RINGING_OUT
    (sub, payload), = world["push"].sent
    assert sub == SUB
    assert payload["kind"] == "ring" and payload["title"] == "Kaki — Mum"
    assert payload["url"].startswith("https://saathi.example.test/family/#call=")
    assert f"call={calls.call_id}" in payload["url"] and "&t=" in payload["url"]
    view = world["panel"].message()["call"]
    assert (view["name"], view["status"], view["number"]) == ("Priya", "ringing", "")
    assert world["hold"].active and world["hold"].seconds == 2.0  # hold to hang up


def test_nothing_rings_while_another_call_is_on(world):
    world["twilio"]["active"] = True
    assert world["calls"].dial(world["member"])["status"] == "busy"
    assert world["push"].sent == [] and not world["calls"].active


def test_a_phone_with_no_alerts_and_no_open_app_is_unreachable(world):
    registry = world["registry"]
    registry.set_subscription(world["member"].id, None)
    result = world["calls"].dial(registry.get(world["member"].id))
    assert result["status"] == "error" and "can't be reached" in result["note"]
    assert not world["calls"].active and world["panel"].message() is None


def test_an_open_app_rings_without_push(world):
    registry = world["registry"]
    registry.set_subscription(world["member"].id, None)
    member = registry.get(world["member"].id)
    phone = Recorder()
    world["calls"].attach(member, phone)
    assert world["calls"].dial(member)["status"] == "calling"
    assert phone.of("type", "ringing")[0]["call_id"] == world["calls"].call_id


def test_a_dead_subscription_is_forgotten_and_the_call_not_left_ringing(world):
    world["push"].fail = PushError("gone", 410)
    result = world["calls"].dial(world["member"])
    assert result["status"] == "error"
    assert world["registry"].get(world["member"].id).subscription is None
    assert not world["calls"].active and not world["hold"].active


def test_accept_starts_the_device_as_offerer_and_the_phone_as_answerer(world):
    calls = world["calls"]
    phone, conn = _phone(world)
    other_tab = Recorder()
    calls.attach(world["member"], other_tab)
    calls.dial(world["member"])
    calls.on_member_message(conn, {"type": "accept", "call_id": calls.call_id})
    assert calls.phase is Phase.CONNECTING
    start = world["device"].of("action", "start")[0]
    assert start["type"] == "rtc" and start["role"] == "offerer" and start["ice_servers"] == ICE
    assert phone.of("type", "start")[0]["role"] == "answerer"
    assert other_tab.of("type", "ended")[0]["reason"] == "answered elsewhere"


def test_signals_pass_only_between_the_two_parties_of_this_call(world):
    calls = world["calls"]
    phone, conn = _connect_out(world)
    call_id = calls.call_id
    offer = {"sdp": {"type": "offer", "sdp": "v=0"}}
    calls.on_device_message({"type": "rtc", "action": "signal", "call_id": call_id,
                             "peer": "kiosk", "data": offer})
    assert phone.of("type", "signal")[-1]["data"] == offer
    # A second screen that also answered `start` is told to stand down,
    # and its signals go nowhere.
    calls.on_device_message({"type": "rtc", "action": "ready", "call_id": call_id,
                             "peer": "laptop"})
    assert world["device"].of("action", "end")[-1]["to"] == "laptop"
    calls.on_device_message({"type": "rtc", "action": "signal", "call_id": call_id,
                             "peer": "laptop", "data": {"sdp": {"type": "offer", "sdp": "x"}}})
    assert len(phone.of("type", "signal")) == 1
    # Wrong call id, from the right peer: dropped.
    calls.on_device_message({"type": "rtc", "action": "signal", "call_id": "old",
                             "peer": "kiosk", "data": offer})
    assert len(phone.of("type", "signal")) == 1
    # A stranger's socket (another paired member) can't inject into the call.
    stranger_member, _ = world["registry"].redeem(
        world["registry"].issue_pairing_token()[0], "Ravi"
    )
    stranger = calls.attach(stranger_member, Recorder())
    calls.on_member_message(stranger, {"type": "signal", "call_id": call_id,
                                       "data": {"candidate": {"candidate": "x"}}})
    assert world["device"].of("action", "signal") == []
    # The bound phone's answer reaches the kiosk, addressed to it.
    answer = {"sdp": {"type": "answer", "sdp": "v=0"}}
    calls.on_member_message(conn, {"type": "signal", "call_id": call_id, "data": answer})
    relayed = world["device"].of("action", "signal")[-1]
    assert relayed["to"] == "kiosk" and relayed["data"] == answer


def test_connected_starts_the_panel_timer(world):
    calls = world["calls"]
    phone, _ = _connect_out(world)
    world["clock"].now = 130.0
    calls.on_device_message({"type": "rtc", "action": "connected", "call_id": calls.call_id,
                             "peer": "kiosk"})
    assert calls.phase is Phase.CONNECTED
    assert phone.of("type", "connected")
    assert world["panel"]._view.connected_at == 130.0


def test_the_end_button_and_the_hold_reach_the_same_hangup(world):
    calls = world["calls"]
    phone, _ = _connect_out(world)
    call_id = calls.call_id
    assert world["hold"].complete()  # what the panel's End button does
    assert not calls.active
    assert phone.of("type", "ended")[-1] == {"type": "ended", "call_id": call_id,
                                             "reason": "hung up"}
    assert world["device"].of("action", "end")[-1]["call_id"] == call_id
    assert world["panel"].message() is None and not world["hold"].active


def test_an_unanswered_ring_ends_itself_and_leaves_a_missed_call(world):
    calls = world["calls"]
    calls.dial(world["member"])
    world["clock"].now += RING_TIMEOUT_SECONDS - 1
    calls.tick()
    assert calls.active
    world["clock"].now += 1
    calls.tick()
    assert not calls.active
    _deadline = time.monotonic() + 2  # the missed-call push is on a worker thread
    while len(world["push"].sent) < 2 and time.monotonic() < _deadline:
        time.sleep(0.01)
    assert world["push"].sent[-1][1]["kind"] == "missed"


def test_a_call_that_never_connects_ends_and_says_why(world):
    calls = world["calls"]
    phone, _ = _connect_out(world)
    world["clock"].now += CONNECT_TIMEOUT_SECONDS
    calls.tick()
    assert not calls.active
    assert phone.of("type", "ended")[-1]["reason"] == "could not connect"


def test_the_phone_leaving_ends_the_call(world):
    calls = world["calls"]
    _, conn = _connect_out(world)
    calls.detach(conn)
    assert not calls.active


def test_a_push_link_token_can_decline_or_answer_that_call_but_not_start_one(world):
    calls = world["calls"]
    calls.dial(world["member"])
    url = world["push"].sent[0][1]["url"]
    token = url.split("&t=")[1].split("&")[0]
    assert calls.attach_with_call_token(calls.call_id, "wrong", Recorder()) is None
    page = Recorder()
    conn = calls.attach_with_call_token(calls.call_id, token, page)
    assert conn is not None and page.of("type", "ringing")
    calls.on_member_message(conn, {"type": "call"})  # may not start a call
    assert calls.phase is Phase.RINGING_OUT
    assert calls.decline_with_token(calls.call_id, token)
    assert not calls.active


# -- they call her ---------------------------------------------------------------


def _ring_in(world):
    calls = world["calls"]
    phone, conn = _phone(world)
    calls.on_member_message(conn, {"type": "call"})
    assert calls.phase is Phase.RINGING_IN
    return phone, conn


def test_an_incoming_call_rings_shows_a_card_and_never_answers_itself(world):
    calls = world["calls"]
    phone, _ = _ring_in(world)
    assert phone.of("type", "waiting")
    assert world["device"].of("action", "ring")[0]["name"] == "Priya"
    card = world["cards"].current
    assert card.kind == "confirm" and "Priya is calling" in card.title
    assert world["hold"].seconds == ANSWER_HOLD_SECONDS
    assert world["panel"].message() is None  # no End button that would answer
    for _ in range(int(RING_TIMEOUT_SECONDS) - 1):
        world["clock"].now += 1
        calls.tick()
        assert calls.phase is Phase.RINGING_IN  # nothing but her answers it
    assert world["device"].of("action", "start") == []
    world["clock"].now += 1
    calls.tick()
    assert not calls.active
    assert phone.of("type", "ended")[-1]["reason"] == "no answer"


def test_a_tap_on_yes_answers_and_the_hold_becomes_hang_up(world):
    calls = world["calls"]
    phone, _ = _ring_in(world)
    card = world["cards"].current
    assert world["cards"].answer(card.id, {"yes": True}, source="tap")
    assert calls.phase is Phase.CONNECTING
    assert world["device"].of("action", "start")[0]["role"] == "offerer"
    assert phone.of("type", "start")[0]["role"] == "answerer"
    assert world["hold"].seconds == 2.0
    assert world["panel"].message()["call"]["status"] == "connected"


def test_a_held_spacebar_answers(world):
    calls = world["calls"]
    _ring_in(world)
    hold = world["hold"]
    hold.begin()  # the press; the Holding card replaces the incoming card
    assert calls.phase is Phase.RINGING_IN  # replaced is not declined
    assert hold.tick(ANSWER_HOLD_SECONDS)
    assert calls.phase is Phase.CONNECTING


def test_a_too_short_tap_does_nothing_and_the_card_comes_back(world):
    calls = world["calls"]
    _ring_in(world)
    hold = world["hold"]
    hold.begin()
    hold.abandon()
    assert world["cards"].current is None and calls.phase is Phase.RINGING_IN
    calls.tick()
    assert "Priya is calling" in world["cards"].current.title


def test_her_spoken_yes_answers_through_answer_card(world):
    calls = world["calls"]
    _ring_in(world)
    tool = make_answer_card_tool(world["cards"], [calls])
    result = tool.handler(yes=True)
    assert result == {"status": "answered", "say": ""}
    assert calls.phase is Phase.CONNECTING


def test_no_declines_and_tells_the_caller(world):
    calls = world["calls"]
    phone, _ = _ring_in(world)
    card = world["cards"].current
    world["cards"].answer(card.id, {"yes": False}, source="tap")
    assert not calls.active
    assert phone.of("type", "ended")[-1]["reason"] == "declined"


def test_a_second_caller_hears_busy(world):
    _ring_in(world)
    other_member, _ = world["registry"].redeem(world["registry"].issue_pairing_token()[0], "Ravi")
    other = Recorder()
    conn = world["calls"].attach(other_member, other)
    world["calls"].on_member_message(conn, {"type": "call"})
    assert other.of("type", "ended")[0]["reason"] == "busy"
