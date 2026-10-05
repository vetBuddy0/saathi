"""The local HTTP/WebSocket server Twilio reaches through the relay:
`/twiml` tells Twilio to open a bidirectional stream, `/media` is that
stream. Port 8768, loopback only — the relay is the only way in.

Exists separately from `screen/server.py` because that server is the
face's, on another port, in another stream's territory, and because a
phone call's socket must not share an event loop with the thing that
has a 100 ms face-reaction budget. `MediaServer` runs the app on its own
thread with its own loop; `build_media_app()` is the testable core — a
fake Twilio peer drives it over `aiohttp.test_utils.TestClient`.

Protocol (Twilio Media Streams, checked against the reference on
2026-09-25): TEXT JSON frames. Inbound `connected`, `start` (carries
`streamSid`, `callSid`, `mediaFormat` = audio/x-mulaw 8000 mono),
`media` (`media.payload` base64 μ-law, 20 ms), `stop`, and `mark`/`dtmf`
we ignore. Outbound `media` (`streamSid` + `media.payload`, same
encoding, no headers) and `clear`. The `/twiml` reply is
`<Response><Connect><Stream url="wss://…/media"/></Connect></Response>`
— `<Connect>`, not `<Start>`: `<Start>` is one-way, and the call ends
when we close the socket, which is the behaviour hang-up wants.

Not here, on purpose: `X-Twilio-Signature` validation on `/twiml`. It
needs the account auth token, which the device does not hold (see
`twilio.py`). Until the production relay validates it, anyone who learns
the tunnel URL could POST to `/twiml` and get TwiML back — harmless
(it contains only the wss URL) — or connect to `/media` and stream
audio to the speaker. A quick tunnel's random hostname is the only
guard today. Recorded as debt, not hidden.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from typing import Callable, Protocol
from xml.sax.saxutils import quoteattr

from aiohttp import WSMsgType, web

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8768


class StreamHandler(Protocol):
    """What the controller implements. All three are called on the media
    server's loop thread; `send_outbound` may be called from any thread."""

    def stream_started(
        self, stream_sid: str, call_sid: str, send_outbound: Callable[[bytes], None]
    ) -> None: ...

    def inbound_audio(self, ulaw: bytes) -> None: ...

    def stream_stopped(self, stream_sid: str) -> None: ...


def twiml_for_stream(ws_url: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(ws_url)}/></Connect></Response>"
    )


def build_media_app(handler: StreamHandler, ws_url: Callable[[], str]) -> web.Application:
    app = web.Application()

    async def healthz(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def twiml(_request: web.Request) -> web.Response:
        return web.Response(text=twiml_for_stream(ws_url()), content_type="text/xml")

    async def media(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=None)
        await ws.prepare(request)
        loop = asyncio.get_running_loop()
        stream_sid: str | None = None

        def send_outbound(ulaw: bytes) -> None:
            if ws.closed or stream_sid is None:
                return
            message = json.dumps(
                {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": base64.b64encode(ulaw).decode("ascii")},
                }
            )
            asyncio.run_coroutine_threadsafe(ws.send_str(message), loop)

        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("dropped malformed media frame")
                    continue
                event = payload.get("event")
                if event == "start":
                    start = payload.get("start") or {}
                    stream_sid = start.get("streamSid") or payload.get("streamSid")
                    if not stream_sid:
                        logger.warning("start event without streamSid; ignoring")
                        continue
                    handler.stream_started(stream_sid, start.get("callSid", ""), send_outbound)
                elif event == "media":
                    encoded = (payload.get("media") or {}).get("payload")
                    if isinstance(encoded, str):
                        try:
                            handler.inbound_audio(base64.b64decode(encoded))
                        except ValueError:
                            logger.warning("dropped undecodable media payload")
                elif event == "stop":
                    break
                # connected / mark / dtmf: nothing to do
        finally:
            if stream_sid is not None:
                handler.stream_stopped(stream_sid)
            if not ws.closed:
                await ws.close()
        return ws

    app.router.add_get("/healthz", healthz)
    app.router.add_post("/twiml", twiml)
    app.router.add_get("/twiml", twiml)
    app.router.add_get("/media", media)
    return app


class MediaServer:
    """Runs the app on a daemon thread with its own event loop.
    `port=0` picks a free port (tests); the product uses `DEFAULT_PORT`
    because the relay is told which local port to forward to."""

    def __init__(
        self,
        handler: StreamHandler,
        ws_url: Callable[[], str],
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
    ) -> None:
        self._handler = handler
        self._ws_url = ws_url
        self._host = host
        self._port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: web.AppRunner | None = None
        self._bound_port: int | None = None
        self._started = threading.Event()
        self._startup_error: BaseException | None = None

    @property
    def port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("media server is not started")
        return self._bound_port

    @property
    def running(self) -> bool:
        return self._loop is not None

    def start(self) -> None:
        if self._loop is not None:
            return
        self._started.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="saathi-media")
        self._thread.start()
        self._started.wait(10.0)
        if self._startup_error is not None:
            error, self._startup_error = self._startup_error, None
            raise error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = web.AppRunner(build_media_app(self._handler, self._ws_url))
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, host=self._host, port=self._port)
            loop.run_until_complete(site.start())
            self._runner = runner
            assert site._server is not None  # aiohttp exposes the bound port only here
            self._bound_port = site._server.sockets[0].getsockname()[1]
            self._loop = loop
        except BaseException as exc:
            self._startup_error = exc
            self._started.set()
            loop.close()
            return
        self._started.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

    def stop(self) -> None:
        loop, self._loop = self._loop, None
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._bound_port = None
        self._thread = None
