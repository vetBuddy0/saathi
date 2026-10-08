"""The family app's server: the one part of Saathi a family member's
phone talks to, over the internet.

Exists as its own aiohttp app on its own port and thread, separate from
the screen server, because this is the port the tunnel exposes. The
screen server's socket can move her face, write her preferences and
press her spacebar; putting the family routes on it would put all of
that on the internet behind one URL. Here, everything public is the
PWA's static files, pairing (one-time token required), push subscription
(member key required) and the signaling socket (member key, or a
per-call token from a push link). Issuing a pairing token is *not*
public: `install_local_routes` adds it to the screen server, which only
listens on loopback and is never tunnelled -- the QR code is minted for
the person standing at the device.

Contested: its own thread and loop, like `call/media.py`, not the
screen server's loop. Signaling is a handful of messages per call and
would fit on the face's loop, but the static files and the pairing
POSTs are internet traffic, and the face's 100 ms budget shouldn't
share a loop with whatever the internet sends.

The public URL. Push links, the QR code and the PWA's origin all need
one stable URL; a cloudflared *quick* tunnel changes hostname on every
start. So:
- `SAATHI_PUBLIC_URL` set (a named tunnel, or any reverse proxy the
  owner runs) -> that URL, no tunnel process started here;
- unset -> a quick tunnel to this server's port (`call/relay.py`), and
  the pairing screen says plainly that a pairing made now only works
  until the device restarts. Rings still reach a phone paired on an
  older hostname -- push doesn't care about our hostname, and the
  notification carries the current URL plus a per-call token -- but
  the family member can't *start* a call from their installed app until
  they pair again. docs/DEMO.md has the named-tunnel steps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable

from aiohttp import WSMsgType, web

from saathi.call.family import PAIRING_TTL_SECONDS, FamilyRegistry, PairingError
from saathi.call.push import PushSender, Subscription
from saathi.call.webrtc import FamilyCalls

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8769
APP_DIR = Path(__file__).resolve().parent.parent / "screen" / "static" / "family"
AUTH_TIMEOUT_SECONDS = 10.0
MAX_MESSAGE_BYTES = 64 * 1024

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self' wss: https:; img-src 'self' data:; "
        "media-src 'self' blob: mediastream:; style-src 'self'; script-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def public_url_from_env(env: dict[str, str] | None = None) -> str | None:
    """`SAATHI_PUBLIC_URL`, trimmed, https only (a service worker and
    push need a secure origin). None when unset."""
    env = os.environ if env is None else env
    value = env.get("SAATHI_PUBLIC_URL", "").strip().rstrip("/")
    if not value:
        return None
    if not value.startswith("https://"):
        raise ValueError("SAATHI_PUBLIC_URL must start with https://")
    return value


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


async def _read_json(request: web.Request) -> dict | None:
    if request.content_length is not None and request.content_length > MAX_MESSAGE_BYTES:
        return None
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_family_app(
    registry: FamilyRegistry,
    calls: FamilyCalls,
    push: PushSender | None,
    *,
    app_dir: Path = APP_DIR,
    run_blocking: Callable[..., Any] | None = None,
) -> web.Application:
    """`run_blocking(fn, *args)` runs a blocking call off the loop
    (default: the loop's executor). Registry calls are SQLite; small,
    but still disk."""

    @web.middleware
    async def headers(request: web.Request, handler):
        response = await handler(request)
        if not isinstance(response, web.WebSocketResponse):
            for key, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    app = web.Application(middlewares=[headers], client_max_size=MAX_MESSAGE_BYTES)

    async def blocking(fn, *args):
        if run_blocking is not None:
            return run_blocking(fn, *args)
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def root(_request: web.Request) -> web.Response:
        raise web.HTTPFound("/family/")

    async def index(_request: web.Request) -> web.FileResponse:
        response = web.FileResponse(app_dir / "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    async def service_worker(_request: web.Request) -> web.FileResponse:
        response = web.FileResponse(app_dir / "sw.js")
        response.headers["Content-Type"] = "text/javascript"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/family/"
        return response

    async def manifest(_request: web.Request) -> web.FileResponse:
        response = web.FileResponse(app_dir / "manifest.webmanifest")
        response.headers["Content-Type"] = "application/manifest+json"
        return response

    async def pair(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if data is None:
            return _json_error(400, "Something went wrong. Please try again.")
        try:
            member, key = await blocking(
                registry.redeem,
                data.get("token"),
                data.get("name"),
                data.get("relation"),
                data.get("calls_her"),
            )
        except PairingError as exc:
            return _json_error(400, str(exc))
        logger.info("family member paired: %s", member.label)
        return web.json_response(
            {
                "ok": True,
                "member_id": member.id,
                "key": key,
                "name": member.name,
                "calls_her": member.calls_her or "",
                "vapid_public_key": push.public_key if push is not None else None,
            }
        )

    async def subscribe(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if data is None:
            return _json_error(400, "bad request")
        member = await blocking(registry.authenticate, data.get("member_id"), data.get("key"))
        if member is None:
            return _json_error(403, "This phone is no longer paired. Ask for a new code.")
        try:
            subscription = Subscription.from_json(data.get("subscription"))
        except ValueError:
            return _json_error(400, "bad subscription")
        await blocking(registry.set_subscription, member.id, subscription)
        return web.json_response({"ok": True})

    async def whoami(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if data is None:
            return _json_error(400, "bad request")
        member = await blocking(registry.authenticate, data.get("member_id"), data.get("key"))
        if member is None:
            return _json_error(403, "This phone is no longer paired. Ask for a new code.")
        return web.json_response(
            {
                "ok": True,
                "name": member.name,
                "calls_her": member.calls_her or "",
                "subscribed": member.subscription is not None,
                "vapid_public_key": push.public_key if push is not None else None,
            }
        )

    async def decline(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if data is None:
            return _json_error(400, "bad request")
        ok = calls.decline_with_token(data.get("call_id"), data.get("call_token"))
        return web.json_response({"ok": ok})

    async def socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE_BYTES, heartbeat=20.0)
        await ws.prepare(request)
        loop = asyncio.get_running_loop()

        def send(message: dict) -> None:
            text = json.dumps(message)

            def deliver() -> None:
                if not ws.closed:
                    asyncio.ensure_future(ws.send_str(text))

            if not loop.is_closed():
                loop.call_soon_threadsafe(deliver)

        conn = None
        try:
            try:
                first = await ws.receive(timeout=AUTH_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                await ws.close(code=4001, message=b"auth timeout")
                return ws
            hello = _parse(first)
            if hello is None or hello.get("type") != "hello":
                await ws.close(code=4001, message=b"auth required")
                return ws
            if hello.get("call_token"):
                conn = calls.attach_with_call_token(
                    hello.get("call_id"), hello.get("call_token"), send
                )
            else:
                member = await blocking(
                    registry.authenticate, hello.get("member"), hello.get("key")
                )
                conn = calls.attach(member, send) if member is not None else None
            if conn is None:
                await ws.send_str(json.dumps({"type": "unauthorized"}))
                await ws.close(code=4003, message=b"not paired")
                return ws
            await ws.send_str(
                json.dumps(
                    {
                        "type": "welcome",
                        "name": conn.member.name,
                        "calls_her": conn.member.calls_her or "",
                        "can_call": conn.call_id is None,
                    }
                )
            )
            # attach() may have queued a "ringing" before the welcome;
            # the page handles either order.
            async for msg in ws:
                payload = _parse(msg)
                if payload is not None:
                    calls.on_member_message(conn, payload)
        finally:
            if conn is not None:
                calls.detach(conn)
        return ws

    app.router.add_get("/", root)
    app.router.add_get("/family", root)
    app.router.add_get("/family/", index)
    app.router.add_get("/family/sw.js", service_worker)
    app.router.add_get("/family/manifest.webmanifest", manifest)
    app.router.add_post("/family/api/pair", pair)
    app.router.add_post("/family/api/subscribe", subscribe)
    app.router.add_post("/family/api/me", whoami)
    app.router.add_post("/family/api/decline", decline)
    app.router.add_get("/family/ws", socket)
    app.router.add_static("/family/static/", app_dir / "static")
    return app


def _parse(msg) -> dict | None:
    if msg.type != WSMsgType.TEXT:
        return None
    try:
        data = json.loads(msg.data)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class FamilyServer:
    """The app above on its own thread and loop (see the docstring)."""

    def __init__(self, app_factory: Callable[[], web.Application], port: int = DEFAULT_PORT,
                 host: str = "127.0.0.1") -> None:
        self._app_factory = app_factory
        self._host = host
        self._port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._error: BaseException | None = None
        self.bound_port: int | None = None

    def start(self) -> None:
        if self._loop is not None:
            return
        self._started.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="saathi-family")
        self._thread.start()
        self._started.wait(10.0)
        if self._error is not None:
            error, self._error = self._error, None
            raise error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = web.AppRunner(self._app_factory())
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, host=self._host, port=self._port)
            loop.run_until_complete(site.start())
            assert site._server is not None
            self.bound_port = site._server.sockets[0].getsockname()[1]
            self._loop = loop
        except BaseException as exc:
            self._error = exc
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
        self._thread = None


# -- the kiosk's side (screen server, loopback only) -------------------------


def qr_svg(text: str) -> str:
    import segno

    qr = segno.make(text, error="m")
    # `omitsize` swaps the fixed width/height for a viewBox, so the page's
    # CSS scales the whole code. With a fixed 392 px size and no viewBox,
    # the 360 px box cropped it -- found live, 2026-10-08: unscannable.
    return qr.svg_inline(scale=8, border=4, dark="#2b1d14", light="#fff8ef", omitsize=True)


def install_local_routes(
    app: web.Application,
    registry: FamilyRegistry,
    public_url: Callable[[], str | None],
    *,
    stable: bool,
    unavailable_reason: Callable[[], str | None] = lambda: None,
) -> None:
    """Adds the pairing screen's two endpoints to the *screen* server:
    POST /family-local/pairing (a fresh QR) and POST
    /family-local/unpair. Never added to the family app (see the module
    docstring)."""

    def _issue() -> dict:
        base = public_url()
        members = [{"id": m.id, "label": m.label} for m in registry.members()]
        if base is None:
            return {
                "ok": False,
                "reason": unavailable_reason() or "The family app is still starting up.",
                "members": members,
            }
        token, _expires = registry.issue_pairing_token(PAIRING_TTL_SECONDS)
        url = f"{base}/family/#pair={token}"
        return {
            "ok": True,
            "url": url,
            "qr_svg": qr_svg(url),
            "expires_in": int(PAIRING_TTL_SECONDS),
            "stable": stable,
            "note": "" if stable else (
                "This address changes when Saathi restarts. A phone paired now can "
                "answer calls, but will need pairing again after a restart to start "
                "calls. Set SAATHI_PUBLIC_URL for a permanent address."
            ),
            "members": members,
        }

    async def pairing(request: web.Request) -> web.Response:
        if not _is_loopback(request):
            raise web.HTTPForbidden()
        result = await asyncio.get_running_loop().run_in_executor(None, _issue)
        return web.json_response(result)

    async def unpair(request: web.Request) -> web.Response:
        if not _is_loopback(request):
            raise web.HTTPForbidden()
        data = await _read_json(request)
        member_id = data.get("member_id") if data else None
        if not isinstance(member_id, str):
            return _json_error(400, "bad request")
        ok = await asyncio.get_running_loop().run_in_executor(None, registry.revoke, member_id)
        return web.json_response({"ok": ok})

    app.router.add_post("/family-local/pairing", pairing)
    app.router.add_post("/family-local/unpair", unpair)


def _is_loopback(request: web.Request) -> bool:
    """Belt and braces: the screen server binds loopback already. A
    request the tunnel forwarded would also come from loopback, which is
    why these routes are on the screen server and never the tunnelled
    one; the Cf-* header check catches a misconfigured tunnel pointed
    at the wrong port."""
    if any(name.lower().startswith("cf-") for name in request.headers):
        return False
    return request.remote in ("127.0.0.1", "::1")
