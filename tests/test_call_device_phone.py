"""call/device_phone.py: "call my son" over the SIM of the device the
engine runs on. The route is ready only while a connected shell has said
it can call; a dial is one `/ws` frame carrying the number, and the
number never reaches a tool result."""

import asyncio
import re

from aiohttp.test_utils import TestClient, TestServer

from saathi.call import contacts
from saathi.call.choosing import ChoiceFlow
from saathi.call.device_phone import (
    DIAL_DEBOUNCE_SECONDS,
    NOT_CONNECTED,
    DevicePhone,
    DevicePhoneError,
)
from saathi.core import Core
from saathi.screen.cards import CardController
from saathi.screen.server import build_app
from saathi.tools.calling import make_call_tool, make_contact_dialer

SON = "+6589614304"
_NUMBER_SHAPE = re.compile(r"\+?[0-9]{8,}")


def _phone():
    now = {"t": 100.0}
    phone = DevicePhone(clock=lambda: now["t"])
    sent = []
    phone.set_broadcast(sent.append)
    return phone, sent, now


def test_not_ready_until_a_shell_says_it_can_call_and_not_after_it_leaves():
    phone, sent, _ = _phone()
    assert phone.ready() == NOT_CONNECTED
    phone.on_status("shell", True)
    assert phone.ready() is None
    phone.on_status("face page", False)  # another client's no changes nothing
    assert phone.ready() is None
    phone.on_status("shell", False)
    assert phone.ready() == NOT_CONNECTED


def test_no_screen_installed_is_not_ready_even_with_a_yes():
    phone = DevicePhone()
    phone.on_status("shell", True)
    assert phone.ready() == NOT_CONNECTED


def test_a_dial_is_one_frame_with_the_number_then_busy_for_a_while():
    phone, sent, now = _phone()
    phone.on_status("shell", True)
    assert not phone.active
    phone.dial(SON, who="Udhi")
    assert sent == [{"type": "phone", "action": "dial", "number": SON}]
    assert phone.active
    now["t"] += DIAL_DEBOUNCE_SECONDS + 1
    assert not phone.active


def test_a_dial_with_no_capable_shell_raises_and_sends_nothing():
    phone, sent, _ = _phone()
    try:
        phone.dial(SON, who="Udhi")
    except DevicePhoneError:
        pass
    else:
        raise AssertionError("dialled with no shell attached")
    assert sent == [] and not phone.active


def test_the_test_number_comes_from_the_twilio_variable(monkeypatch):
    phone, sent, _ = _phone()
    phone.on_status("shell", True)
    monkeypatch.delenv("TWILIO_TEST_NUMBER", raising=False)
    try:
        phone.dial_test_number()
    except DevicePhoneError as exc:
        assert "test number" in str(exc)
    else:
        raise AssertionError("dialled an unset test number")
    monkeypatch.setenv("TWILIO_TEST_NUMBER", "+6590000000")
    phone.dial_test_number()
    assert sent[-1]["number"] == "+6590000000"


def _tool(tmp_path, phone):
    path = tmp_path / "identity.sqlite3"
    contacts.save_contact(path, "Udhi", SON, "SG", "son")
    choices = ChoiceFlow(CardController(), make_contact_dialer(phone, None, phone.ready))
    return make_call_tool(phone, path, choices, phone_ready=phone.ready)


def test_call_my_son_dials_his_saved_number_and_the_result_carries_no_number(tmp_path):
    phone, sent, _ = _phone()
    phone.on_status("shell", True)
    reply = _tool(tmp_path, phone).handler(contact="my son")
    assert reply["status"] == "calling" and "Calling Udhi." in reply["note"]
    assert not _NUMBER_SHAPE.search(str(reply))
    assert sent == [{"type": "phone", "action": "dial", "number": SON}]


def test_a_second_call_while_the_first_rings_out_is_busy_not_a_second_dial(tmp_path):
    phone, sent, _ = _phone()
    phone.on_status("shell", True)
    tool = _tool(tmp_path, phone)
    tool.handler(contact="my son")
    assert tool.handler(contact="Udhi")["status"] == "busy"
    assert len(sent) == 1


def test_without_a_capable_shell_she_is_told_plainly_and_nothing_dials(tmp_path):
    phone, sent, _ = _phone()
    reply = _tool(tmp_path, phone).handler(contact="my son")
    assert reply["status"] == "unavailable" and NOT_CONNECTED in reply["note"]
    assert sent == []


async def _drain_connect(ws) -> None:
    assert (await ws.receive_json())["type"] == "state"
    assert (await ws.receive_json())["type"] == "settings"


async def test_the_server_carries_the_shells_status_and_the_dial_frame_and_forgets_on_close():
    phone = DevicePhone()
    app = build_app(Core(), phone=phone)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as shell, client.ws_connect("/ws") as face:
            await _drain_connect(shell)
            await _drain_connect(face)
            assert phone.ready() == NOT_CONNECTED
            await shell.send_json({"type": "phone_status", "can_call": True})
            await face.send_json({"type": "phone_status", "can_call": "yes"})  # malformed: dropped
            # Applied on the server's loop; wait for it rather than sleep blind.
            for _ in range(50):
                if phone.ready() is None:
                    break
                await asyncio.sleep(0.01)
            assert phone.ready() is None
            phone.dial(SON, who="Udhi")
            expected = {"type": "phone", "action": "dial", "number": SON}
            assert await shell.receive_json() == expected
            assert await face.receive_json() == expected  # every screen; the face ignores it
        for _ in range(50):
            if phone.ready() is not None:
                break
            await asyncio.sleep(0.01)
        assert phone.ready() == NOT_CONNECTED
