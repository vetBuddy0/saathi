"""call/web_phone.py: Twilio calls placed by the face page. The engine
mints the token, picks the number, owns the panel and the one hang-up
path; the page reports what its call did."""

import asyncio
import base64
import hashlib
import hmac
import json

from aiohttp.test_utils import TestClient, TestServer

from saathi.call.twilio import TwilioCredentials
from saathi.call.web_phone import NOT_READY, TOKEN_TTL_SECONDS, WebPhone, WebPhoneError
from saathi.core import Core
from saathi.screen.call_panel import CallPanel
from saathi.screen.cards import CardController, HoldController
from saathi.screen.server import build_app

CREDS = TwilioCredentials(
    "AC" + "0" * 32, "SK" + "1" * 32, "secret", "+1" + "0" * 10, "+6590000000"
)
APP = "AP" + "2" * 32
SON = "+6589614304"


def _phone(now=1_000.0):
    clock = {"t": now}
    panel = CallPanel()
    shown = []
    panel.set_broadcast(shown.append)
    hold = HoldController(CardController())
    phone = WebPhone(
        CREDS,
        APP,
        panel=panel.update,
        hold=hold,
        clock=lambda: clock["t"],
        wall=lambda: 1_700_000_000,
    )
    sent = []
    phone.set_broadcast(sent.append)
    return phone, sent, shown, hold, clock


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_the_token_is_a_signed_outgoing_only_voice_grant():
    phone, *_ = _phone()
    header, payload, signature = phone.token().split(".")
    assert json.loads(_b64decode(header)) == {"typ": "JWT", "alg": "HS256", "cty": "twilio-fpa;v=1"}
    claims = json.loads(_b64decode(payload))
    assert claims["iss"] == CREDS.api_key and claims["sub"] == CREDS.account_sid
    assert claims["exp"] - claims["iat"] == TOKEN_TTL_SECONDS
    assert claims["grants"]["voice"] == {
        "incoming": {"allow": False},
        "outgoing": {"application_sid": APP},
    }
    expected = hmac.new(b"secret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert _b64decode(signature) == expected


def test_only_a_twilio_status_makes_it_ready_and_a_sim_one_does_not():
    phone, *_ = _phone()
    phone.on_status("shell", True, None)  # the Android shell's SIM status
    assert phone.ready() == NOT_READY
    phone.on_status("page", True, "twilio")
    assert phone.ready() is None


def test_a_dial_shows_the_panel_arms_the_hold_and_sends_one_frame():
    phone, sent, shown, hold, _ = _phone()
    phone.on_status("page", True, "twilio")
    phone.dial(SON, who="Udhi")
    assert phone.active and hold.active
    assert len(sent) == 1
    frame = sent[0]
    assert frame["type"] == "phone" and frame["via"] == "twilio" and frame["action"] == "dial"
    assert frame["number"] == SON and frame["id"]
    assert shown[-1]["call"]["name"] == "Udhi" and shown[-1]["call"]["status"] == "calling"
    try:
        phone.dial(SON, who="Udhi")
    except WebPhoneError:
        pass
    else:
        raise AssertionError("a second dial during a call went out")


def test_the_pages_events_move_the_panel_and_a_stale_id_is_dropped():
    phone, sent, shown, hold, clock = _phone()
    phone.on_status("page", True, "twilio")
    phone.dial(SON, who="Udhi")
    call_id = sent[0]["id"]
    phone.on_event({"id": "someone-else", "event": "ended"})
    assert phone.active
    phone.on_event({"id": call_id, "event": "ringing"})
    assert shown[-1]["call"]["status"] == "ringing"
    clock["t"] += 5
    phone.on_event({"id": call_id, "event": "connected"})
    assert shown[-1]["call"]["status"] == "connected"
    phone.on_event({"id": call_id, "event": "ended"})
    assert not phone.active and not hold.active
    assert shown[-1] == {"type": "call", "call": None}


def test_the_end_button_and_a_hold_reach_the_same_hangup():
    phone, sent, shown, hold, _ = _phone()
    phone.on_status("page", True, "twilio")
    phone.dial(SON, who="Udhi")
    assert hold.complete() is True  # what the panel's End button fires
    assert sent[-1] == {"type": "phone", "via": "twilio", "action": "hangup", "id": sent[0]["id"]}
    assert not phone.active and not hold.active
    assert shown[-1] == {"type": "call", "call": None}


def test_the_page_going_away_mid_call_ends_it():
    phone, sent, shown, hold, _ = _phone()
    phone.on_status("page", True, "twilio")
    phone.dial(SON, who="Udhi")
    phone.on_status("page", False)
    assert not phone.active and not hold.active and phone.ready() == NOT_READY


def test_the_test_number_is_twilios():
    phone, sent, *_ = _phone()
    phone.on_status("page", True, "twilio")
    phone.dial_test_number()
    assert sent[-1]["number"] == CREDS.test_number


async def test_the_server_hands_the_token_to_loopback_and_carries_the_pages_reports():
    phone, *_ = _phone()
    sent = []
    app = build_app(Core(), phone=phone)
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        response = await client.get("/phone/token")
        assert response.status == 200
        assert (await response.json())["token"].count(".") == 2
        assert response.headers["Cache-Control"] == "no-store"
        async with client.ws_connect("/ws") as ws:
            assert (await ws.receive_json())["type"] == "state"
            assert (await ws.receive_json())["type"] == "settings"
            await ws.send_json({"type": "phone_status", "via": "twilio", "can_call": True})
            for _ in range(50):
                if phone.ready() is None:
                    break
                await asyncio.sleep(0.01)
            assert phone.ready() is None
            phone.set_broadcast(sent.append)  # watch the frames directly
            phone.dial(SON, who="Udhi")
            await ws.send_json({"type": "phone_event", "id": sent[0]["id"], "event": "ended"})
            for _ in range(50):
                if not phone.active:
                    break
                await asyncio.sleep(0.01)
            assert not phone.active


async def test_an_engine_without_twilio_has_no_token_route():
    from saathi.call.device_phone import DevicePhone

    app = build_app(Core(), phone=DevicePhone())
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        assert (await client.get("/phone/token")).status == 404
