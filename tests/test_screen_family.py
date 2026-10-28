"""A family-app call through the real screen server: the kiosk's half of
the signaling rides `/ws` as `{"type": "rtc"}`, the phone panel shows
the call, and the panel's End button hangs it up through the same hold
seam a Twilio call uses. Plus `saathi run`'s wiring with the family app
switched on."""

from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from saathi import cli
from saathi.call.family import FamilyRegistry
from saathi.call.family_server import install_local_routes
from saathi.call.push import Subscription
from saathi.call.webrtc import FamilyCalls, Phase
from saathi.core import Core
from saathi.screen.call_panel import CallPanel
from saathi.screen.cards import CardController, HoldController
from saathi.screen.server import build_app
from tests.test_cli import seams  # noqa: F401  (fixture)

SUB = Subscription("https://push.test/endpoint", b"\x04" + b"\x01" * 64, b"\x02" * 16)


class FakePush:
    public_key = "K"

    def send(self, *_a, **_k):
        pass


class Family:
    """The three seams `server.py` uses, as `FamilyRuntime` provides them."""

    def __init__(self, calls, registry):
        self.calls = calls
        self.registry = registry

    def set_broadcast(self, send):
        self.calls.set_broadcast(send)

    def on_device_message(self, payload):
        self.calls.on_device_message(payload)

    def install_local_routes(self, app):
        install_local_routes(app, self.registry, lambda: "https://s.test", stable=True)


async def _until(ws, predicate):
    while True:
        message = await asyncio.wait_for(ws.receive_json(), 2)
        if predicate(message):
            return message


async def test_a_family_call_signals_through_the_kiosk_socket_and_ends_from_the_panel(tmp_path):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    member, _ = registry.redeem(registry.issue_pairing_token()[0], "Priya", "daughter")
    registry.set_subscription(member.id, SUB)
    member = registry.get(member.id)
    cards = CardController()
    hold = HoldController(cards)
    panel = CallPanel()
    calls = FamilyCalls(registry, push=FakePush(), public_url=lambda: "https://s.test",
                        hold=hold, cards=cards, panel=panel.update)
    calls._ensure_ticker = lambda: None
    phone_messages = []
    conn = calls.attach(member, phone_messages.append)
    app = build_app(Core(), cards=cards, hold=hold, calls=panel,
                    family=Family(calls, registry))
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as kiosk:
            await _until(kiosk, lambda m: m["type"] == "settings")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, calls.dial, member)
            shown = await _until(kiosk, lambda m: m["type"] == "call" and m["call"])
            assert shown["call"]["name"] == "Priya"
            calls.on_member_message(conn, {"type": "accept", "call_id": calls.call_id})
            start = await _until(kiosk, lambda m: m.get("action") == "start")
            assert start["type"] == "rtc" and start["role"] == "offerer"
            await kiosk.send_json({"type": "rtc", "action": "ready", "call_id": calls.call_id,
                                   "peer": "kiosk"})
            offer = {"sdp": {"type": "offer", "sdp": "v=0"}}
            await kiosk.send_json({"type": "rtc", "action": "signal",
                                   "call_id": calls.call_id, "peer": "kiosk", "data": offer})
            for _ in range(100):
                if any(m.get("type") == "signal" for m in phone_messages):
                    break
                await asyncio.sleep(0.01)
            assert [m["data"] for m in phone_messages if m["type"] == "signal"] == [offer]
            # The panel's End button: same message, same seam, as a phone call.
            await kiosk.send_json({"type": "call_hangup", "id": panel.current_id})
            ended = await _until(kiosk, lambda m: m.get("action") == "end")
            assert ended["type"] == "rtc"
            assert calls.phase is Phase.IDLE
            assert phone_messages[-1]["type"] == "ended"
        pairing = await client.post("/family-local/pairing")
        assert (await pairing.json())["stable"] is True


def test_saathi_run_wires_the_family_app_when_it_is_switched_on(seams, monkeypatch):  # noqa: F811
    from saathi.call import family_runtime

    monkeypatch.setenv("SAATHI_FAMILY_APP", "on")
    monkeypatch.setenv("SAATHI_PUBLIC_URL", "https://saathi.example.test")
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_API_KEY", "TWILIO_API_SECRET",
                 "TWILIO_FROM_NUMBER", "TWILIO_TEST_NUMBER"):
        monkeypatch.delenv(name, raising=False)
    started = []
    monkeypatch.setattr(family_runtime.FamilyRuntime, "start_in_background",
                        lambda self: started.append(self))
    runtime = cli.build_runtime()
    family = runtime.family
    assert family is not None and started == [family]
    assert family.calls._hold is runtime.hold and family.calls._cards is runtime.cards
    assert {"call_contact", "save_contact", "answer_card"} <= {t.name for t in runtime.registry}
    # No Twilio: an unpaired name is an honest no, never a crash.
    reply = runtime.handle_intent("call_contact", {"contact": "my son"})
    assert reply["status"] in ("unavailable", "no_match")
    # Paired, but the app isn't up yet: "starting up".
    registry = family.registry
    registry.redeem(registry.issue_pairing_token()[0], "Priya", "daughter")
    reply = runtime.handle_intent("call_contact", {"contact": "my daughter"})
    assert reply["status"] == "unavailable" and "starting up" in reply["note"]
    family.ready.set()
    reply = runtime.handle_intent("call_contact", {"contact": "my daughter"})
    # Paired but alerts never turned on and the app not open: unreachable.
    assert reply["status"] == "error" and "can't be reached" in reply["note"]


def test_the_family_app_is_off_unless_switched_on(seams):  # noqa: F811
    runtime = cli.build_runtime()
    assert runtime.family is None
