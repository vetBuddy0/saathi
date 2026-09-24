"""saathi/call/controller.py — dial, bridge, hang up, all against fakes;
the fake-peer end to end lives here too."""

import base64
import json
import threading

import pytest

from saathi.call.controller import CallController, CallState
from saathi.call.hangup import HANGUP_HOLD_SECONDS, FakeHoldSeam
from saathi.call.media import build_media_app
from saathi.call.relay import FakeRelay
from saathi.call.twilio import FakeTwilioClient, TwilioCredentials, TwilioError
from aiohttp.test_utils import TestClient, TestServer

CREDS = TwilioCredentials(
    account_sid="AC" + "0" * 32,
    api_key="SK" + "1" * 32,
    api_secret="s",
    from_number="+1" + "0" * 10,
    test_number="+1" + "0" * 9 + "1",
)


class FakeServer:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class FakeCallAudio:
    instances: list["FakeCallAudio"] = []

    def __init__(self):
        self.send = None
        self.inbound: list[bytes] = []
        self.stopped = False
        FakeCallAudio.instances.append(self)

    def start(self, send):
        self.send = send

    def feed_inbound(self, ulaw):
        self.inbound.append(ulaw)

    def stop(self):
        self.stopped = True


@pytest.fixture
def parts():
    FakeCallAudio.instances.clear()
    client = FakeTwilioClient()
    relay = FakeRelay("https://relay.example.test")
    server = FakeServer()
    hold = FakeHoldSeam()
    controller = CallController(CREDS, client, relay, server, FakeCallAudio, hold)
    return controller, client, relay, server, hold


def test_dial_brings_up_relay_and_server_then_places_the_call_with_the_twiml_url(parts):
    controller, client, relay, server, hold = parts
    sid = controller.dial_test_number()
    assert sid == client.next_sid
    assert client.created == [(CREDS.test_number, CREDS.from_number, "https://relay.example.test/twiml")]
    assert (relay.started, server.started) == (1, 1)
    assert controller.state is CallState.DIALLING and controller.active
    assert hold.handler is not None and hold.seconds == HANGUP_HOLD_SECONDS
    assert controller.ws_url() == "wss://relay.example.test/media"


def test_a_second_dial_while_active_is_refused(parts):
    controller, *_ = parts
    controller.dial_test_number()
    with pytest.raises(TwilioError):
        controller.dial_test_number()


def test_an_injected_client_error_containing_a_url_comes_out_sanitized(parts):
    controller, client, *_ = parts
    client.fail_with = RuntimeError(
        "POST https://api.twilio.com/2010-04-01/Accounts/AC0/Calls.json To=%2B10000000001"
    )
    with pytest.raises(TwilioError) as info:
        controller.dial_test_number()
    text = str(info.value)
    assert "twilio.com" not in text and "10000000001" not in text and "http" not in text
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert controller.state is CallState.IDLE and not controller.active


def test_stream_start_bridges_audio_and_stop_releases_it(parts):
    controller, client, relay, server, hold = parts
    controller.dial_test_number()
    sent = []
    controller.stream_started("MZ1", "CA1", sent.append)
    audio = FakeCallAudio.instances[-1]
    assert controller.state is CallState.IN_CALL
    assert audio.send == sent.append
    controller.inbound_audio(b"\xff" * 160)
    assert audio.inbound == [b"\xff" * 160]
    controller.stream_stopped("MZ1")  # the far end hung up
    assert audio.stopped
    assert controller.state is CallState.IDLE and hold.cleared == 1
    assert client.completed == []  # Twilio already ended it; nothing to complete


def test_a_stream_for_no_call_is_not_bridged(parts):
    controller, *_ = parts
    controller.stream_started("MZ9", "CA9", lambda _b: None)
    assert FakeCallAudio.instances == [] and controller.state is CallState.IDLE


def test_a_two_second_hold_hangs_up_and_a_tap_does_nothing(parts):
    controller, client, relay, server, hold = parts
    controller.dial_test_number()
    controller.stream_started("MZ1", "CA1", lambda _b: None)
    audio = FakeCallAudio.instances[-1]
    assert hold.simulate_hold(0.3) is False  # a single tap during a call
    assert controller.state is CallState.IN_CALL and client.completed == []
    assert hold.simulate_hold(HANGUP_HOLD_SECONDS) is True
    assert client.completed == [client.next_sid]
    assert audio.stopped and controller.state is CallState.IDLE
    assert hold.handler is None
    controller.stream_stopped("MZ1")  # Twilio's stop arrives after: harmless


def test_hangup_survives_a_failing_complete_call(parts):
    controller, client, *_ = parts
    controller.dial_test_number()
    client.fail_with = RuntimeError("https://api.twilio.com/... 500")
    controller.hangup()  # logs sanitized, does not raise
    assert controller.state is CallState.IDLE


def test_shutdown_hangs_up_and_tears_down_relay_and_server(parts):
    controller, client, relay, server, hold = parts
    controller.dial_test_number()
    controller.shutdown()
    assert client.completed == [client.next_sid]
    assert (relay.stopped, server.stopped) == (1, 1)


def test_state_changes_are_observable(parts):
    controller, *_ = parts
    seen = []
    controller.on_state_change = seen.append
    controller.dial_test_number()
    controller.stream_started("MZ1", "CA1", lambda _b: None)
    controller.hangup()
    assert seen == [CallState.DIALLING, CallState.IN_CALL, CallState.IDLE]


async def test_end_to_end_with_a_fake_twilio_peer(parts):
    """Dial from a worker thread (as the tool handler does), then a fake
    Twilio connects to /media, audio flows both ways, a 2 s hold hangs
    up, Twilio's stop arrives, everything is released."""
    controller, client, relay, server, hold = parts
    app = build_media_app(controller, controller.ws_url)

    dialled = threading.Event()
    threading.Thread(target=lambda: (controller.dial_test_number(), dialled.set())).start()
    assert dialled.wait(2.0)

    async with TestClient(TestServer(app)) as peer:
        twiml = await (await peer.post("/twiml")).text()
        assert "wss://relay.example.test/media" in twiml
        async with peer.ws_connect("/media") as ws:
            await ws.send_json({"event": "connected"})
            await ws.send_json(
                {"event": "start", "start": {"streamSid": "MZ1", "callSid": client.next_sid}}
            )
            inbound = bytes(range(160))
            await ws.send_json(
                {"event": "media", "media": {"payload": base64.b64encode(inbound).decode()}}
            )
            for _ in range(100):
                if FakeCallAudio.instances and FakeCallAudio.instances[-1].inbound:
                    break
                await __import__("asyncio").sleep(0.01)
            audio = FakeCallAudio.instances[-1]
            assert controller.state is CallState.IN_CALL
            assert audio.inbound == [inbound]

            outbound = bytes(reversed(range(160)))
            audio.send(outbound)
            message = json.loads(await ws.receive_str())
            assert base64.b64decode(message["media"]["payload"]) == outbound

            assert hold.simulate_hold(2.0)
            assert client.completed == [client.next_sid]
            await ws.send_json({"event": "stop", "streamSid": "MZ1"})
            await ws.receive()
    assert audio.stopped and controller.state is CallState.IDLE and not controller.active
