"""saathi/call/media.py — a fake Twilio peer over aiohttp's TestClient
drives /twiml and /media; MediaServer comes up and down on its own
thread."""

import asyncio
import base64
import json

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from saathi.call.media import MediaServer, build_media_app, twiml_for_stream


class RecordingHandler:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.inbound: list[bytes] = []
        self.stopped: list[str] = []
        self.send = None

    def stream_started(self, stream_sid, call_sid, send_outbound):
        self.started.append((stream_sid, call_sid))
        self.send = send_outbound

    def inbound_audio(self, ulaw):
        self.inbound.append(ulaw)

    def stream_stopped(self, stream_sid):
        self.stopped.append(stream_sid)


def test_twiml_connects_a_bidirectional_stream_to_the_wss_url():
    xml = twiml_for_stream("wss://relay.example.test/media")
    assert "<Connect><Stream url=\"wss://relay.example.test/media\"/></Connect>" in xml
    assert "<Start>" not in xml


async def test_twiml_route_answers_twilios_post_with_xml():
    handler = RecordingHandler()
    app = build_media_app(handler, lambda: "wss://relay.example.test/media")
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/twiml", data={"CallSid": "CAx"})
        assert response.status == 200
        assert response.content_type == "text/xml"
        assert "wss://relay.example.test/media" in await response.text()
        health = await client.get("/healthz")
        assert await health.text() == "ok"


async def test_media_socket_bridges_both_directions_and_reports_stop():
    handler = RecordingHandler()
    app = build_media_app(handler, lambda: "wss://relay.example.test/media")
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/media") as ws:
            await ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            await ws.send_json(
                {
                    "event": "start",
                    "sequenceNumber": "1",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": "CA1",
                        "tracks": ["inbound"],
                        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
                    },
                    "streamSid": "MZ1",
                }
            )
            frame = bytes(range(160))
            await ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZ1",
                    "media": {"track": "inbound", "payload": base64.b64encode(frame).decode()},
                }
            )
            await ws.send_str("not json")
            await ws.send_json({"event": "mark", "streamSid": "MZ1", "mark": {"name": "x"}})
            # Let the server's loop process the frames before asserting.
            for _ in range(50):
                if handler.inbound:
                    break
                await asyncio.sleep(0.01)
            assert handler.started == [("MZ1", "CA1")]
            assert handler.inbound == [frame]

            # Outbound: the send callable the handler was given, called
            # from another thread as the capture thread would.
            outbound = bytes(reversed(range(160)))
            await asyncio.get_running_loop().run_in_executor(None, handler.send, outbound)
            message = json.loads((await ws.receive_str()))
            assert message["event"] == "media"
            assert message["streamSid"] == "MZ1"
            assert base64.b64decode(message["media"]["payload"]) == outbound

            await ws.send_json({"event": "stop", "streamSid": "MZ1", "stop": {"callSid": "CA1"}})
            closed = await ws.receive()
            assert closed.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
        assert handler.stopped == ["MZ1"]


async def test_a_dropped_socket_without_stop_still_reports_stream_stopped():
    handler = RecordingHandler()
    app = build_media_app(handler, lambda: "wss://relay.example.test/media")
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/media")
        await ws.send_json({"event": "start", "start": {"streamSid": "MZ2", "callSid": "CA2"}})
        await asyncio.sleep(0.05)
        await ws.close()
        for _ in range(50):
            if handler.stopped:
                break
            await asyncio.sleep(0.01)
    assert handler.stopped == ["MZ2"]


async def test_media_server_runs_on_its_own_thread_and_stops():
    handler = RecordingHandler()
    server = MediaServer(handler, lambda: "wss://relay.example.test/media", port=0)
    server.start()
    try:
        assert server.running and server.port > 0
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{server.port}/healthz") as response:
                assert await response.text() == "ok"
    finally:
        server.stop()
    assert not server.running
