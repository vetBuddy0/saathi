"""Serves `static/` and holds the WebSocket that connects `core.py` to the
Chromium kiosk.

Two message shapes cross this socket, and it interprets nothing beyond
them: `{"type": "state", "state": "<State value>"}` (server -> browser,
sent on connect and on every real transition) and
`{"type": "input", "event": "press" | "release"}` (browser -> server, one
per spacebar down/up). The browser decides nothing about what a press
*means* — that is `core.py`'s transition table — this module only carries
the event there and the resulting state back.

Checkpoint 1 has no voice engine, so `release` (`LISTENING` -> `THINKING`)
is immediately followed by a synthetic `no_response` event
(`THINKING` -> `IDLE`) rather than waiting for a real answer — SPEC.md's
"spacebar driving the state machine on fake events." Without it, letting go
of the spacebar would leave the face stuck "thinking" forever, which is a
worse experience than the honest one: there is no brain yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

from saathi.core import Core, Event

_STATIC_DIR = Path(__file__).parent / "static"

logger = logging.getLogger(__name__)


def build_app(core: Core) -> web.Application:
    app = web.Application()
    websockets: set[web.WebSocketResponse] = set()

    def broadcast_state(state, _event: Event) -> None:
        message = json.dumps({"type": "state", "state": state.value})
        for ws in list(websockets):
            if not ws.closed:
                asyncio.ensure_future(ws.send_str(message))

    core.subscribe(broadcast_state)

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(_STATIC_DIR / "index.html")

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        websockets.add(ws)
        await ws.send_str(json.dumps({"type": "state", "state": core.state.value}))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("dropped malformed message: %r", msg.data)
                    continue
                if payload.get("type") != "input":
                    continue
                kind = payload.get("event")
                if kind == "press":
                    core.handle(Event("press"))
                elif kind == "release":
                    core.handle(Event("release"))
                    core.handle(Event("no_response"))  # fake: no AI at checkpoint 1
        finally:
            websockets.discard(ws)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static/", _STATIC_DIR)
    return app


def run(core: Core, host: str, port: int) -> None:
    web.run_app(build_app(core), host=host, port=port, print=None)
