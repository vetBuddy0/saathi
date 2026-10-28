"""Twilio calls placed by the face page itself, over the internet.

Why it exists: the owner wants calls to go through Twilio on the Android
app (2026-10-28), and the Twilio path `call/controller.py` built for the
laptop can't run there -- its call audio comes back over a Media Streams
websocket that needs a public tunnel (cloudflared) and the laptop's
echo-cancelled pair. Twilio's Voice JavaScript SDK needs neither: the
face page (a WebView on `http://127.0.0.1`, a secure context, so the
microphone is allowed) registers as a Twilio client and the call's media
is WebRTC straight to Twilio, with the browser's own echo cancelling.
Twilio then dials her son's number from a TwiML App whose Voice URL is a
TwiML Bin: `<Dial callerId="..."><Number>{{To}}</Number></Dial>`. No
server of ours is ever on the public internet.

This object stands where `CallController` (Twilio-from-the-laptop) and
`DevicePhone` (the SIM) stand: `active`, `dial`, `dial_test_number`, so
`tools/calling.py`'s matching, cards and permission check are unchanged.
It owns the call's state for the screen -- the phone panel
(`screen/call_panel.py`) and the hold seam, so the panel's End button and
a two-second hold both reach `hangup()`, the one hang-up path -- and the
page only reports what its `Twilio.Call` did:

    engine -> page   {"type": "phone", "via": "twilio", "action": "dial", "id", "number"}
    engine -> page   {"type": "phone", "via": "twilio", "action": "hangup", "id"}
    page -> engine   {"type": "phone_status", "via": "twilio", "can_call": bool}
    page -> engine   {"type": "phone_event", "id", "event": ringing|connected|ended|failed}
    page -> engine   GET /phone/token  -> {"token": <JWT>}

`via` keeps the Android shell's SIM dialer (`PhoneCalls.kt`) away from
these frames, and keeps its SIM `phone_status` from counting here.

The access token is minted here, not fetched from a token server: it is
an HS256 JWT signed with the API key secret the engine already holds
(Twilio's documented format), valid for an hour, outgoing only -- this
device never receives Twilio calls. Lost: twilio-python's AccessToken
(a dependency for twenty lines, and not in the APK).

Numbers are in the dial frame and the panel (her eyes) only -- never in
a tool result or a log line, as everywhere in `call/`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from saathi.call.hangup import HANGUP_HOLD_SECONDS, HANGUP_LABEL
from saathi.call.twilio import TwilioCredentials
from saathi.screen.call_panel import CallView

logger = logging.getLogger(__name__)

# The TwiML App whose Voice URL dials `To` (module docstring).
APP_SID_VAR = "TWILIO_TWIML_APP_SID"
TOKEN_TTL_SECONDS = 3600
IDENTITY = "saathi-device"
VIA = "twilio"

NOT_READY = "Calling isn't ready on this device yet."


class WebPhoneError(RuntimeError):
    """The page can't take the dial (not registered with Twilio)."""


@dataclass
class _Call:
    id: str
    who: str
    number: str
    status: str = "calling"
    connected_at: float | None = None


def app_sid_from_env(environ: Any = None) -> str | None:
    env = os.environ if environ is None else environ
    value = env.get(APP_SID_VAR, "").strip()
    return value or None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def access_token(creds: TwilioCredentials, app_sid: str, now: float) -> str:
    """A Twilio Voice access token: outgoing calls through `app_sid`."""
    issued = int(now)
    header = {"typ": "JWT", "alg": "HS256", "cty": "twilio-fpa;v=1"}
    payload = {
        "jti": f"{creds.api_key}-{issued}",
        "iss": creds.api_key,
        "sub": creds.account_sid,
        "iat": issued,
        "exp": issued + TOKEN_TTL_SECONDS,
        "grants": {
            "identity": IDENTITY,
            "voice": {"incoming": {"allow": False}, "outgoing": {"application_sid": app_sid}},
        },
    }
    signing = ".".join(
        _b64(json.dumps(part, separators=(",", ":")).encode()) for part in (header, payload)
    )
    signature = hmac.new(creds.api_secret.encode(), signing.encode(), hashlib.sha256).digest()
    return f"{signing}.{_b64(signature)}"


class WebPhone:
    route = "Twilio, from the face page"

    def __init__(
        self,
        creds: TwilioCredentials,
        app_sid: str,
        *,
        panel: Callable[[CallView | None], None] | None = None,
        hold: Any = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._creds = creds
        self._app_sid = app_sid
        self._panel = panel
        self._hold = hold
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._broadcast: Callable[[dict], None] | None = None
        self._capable: set[Any] = set()
        self._call: _Call | None = None

    # -- the page -------------------------------------------------------

    def set_broadcast(self, fn: Callable[[dict], None] | None) -> None:
        with self._lock:
            self._broadcast = fn

    def token(self) -> str:
        return access_token(self._creds, self._app_sid, self._wall())

    def on_status(self, client: Any, can_call: bool, via: str | None = None) -> None:
        """The page saying its Twilio client is (or is no longer)
        registered. A SIM status (`via` not "twilio") is not ours. A
        page that goes away mid-call takes the call with it."""
        if can_call and via != VIA:
            return
        with self._lock:
            if can_call:
                self._capable.add(client)
                return
            self._capable.discard(client)
            orphaned = self._call is not None and not self._capable
        if orphaned:
            self._end("the page that had the call went away")

    def on_event(self, payload: dict) -> None:
        """What the page's `Twilio.Call` did. A stale id is dropped."""
        event = payload.get("event")
        with self._lock:
            call = self._call
            if call is None or payload.get("id") != call.id:
                call = None
            elif event == "ringing" and call.status == "calling":
                call.status = "ringing"
            elif event == "connected" and call.status != "connected":
                call.status = "connected"
                call.connected_at = self._clock()
            elif event not in ("ended", "failed"):
                return
            view = self._view_locked()
        if call is None:
            # The SDK says "disconnect" before the "error" that caused it
            # (seen 2026-10-29: the mic could not open, the call ended in
            # 0.9 s and the reason was dropped here). The call is over
            # either way; the reason still goes in the log.
            if event == "failed":
                logger.warning(
                    "twilio call failed (after it ended): %s",
                    str(payload.get("detail", ""))[:200],
                )
            return
        if event in ("ended", "failed"):
            if event == "failed":
                logger.warning("twilio call failed: %s", str(payload.get("detail", ""))[:200])
            self._end(f"the call {event}")
            return
        self._show(view)

    # -- what tools/calling.py calls ------------------------------------

    def ready(self) -> str | None:
        with self._lock:
            return None if self._capable and self._broadcast is not None else NOT_READY

    @property
    def active(self) -> bool:
        with self._lock:
            return self._call is not None

    def dial(self, number: str, who: str = "") -> None:
        with self._lock:
            broadcast = self._broadcast if self._capable else None
            if broadcast is None or not number:
                raise WebPhoneError(NOT_READY)
            if self._call is not None:
                raise WebPhoneError("A call is already in progress.")
            call = _Call(uuid.uuid4().hex, who or "the number", number)
            self._call = call
            view = self._view_locked()
        logger.info("asking the page to call %s over Twilio", call.who)
        self._show(view)
        if self._hold is not None:
            self._hold.set_handler(self.hangup, seconds=HANGUP_HOLD_SECONDS, label=HANGUP_LABEL)
        broadcast(
            {"type": "phone", "via": VIA, "action": "dial", "id": call.id, "number": number}
        )

    def dial_test_number(self) -> None:
        self.dial(self._creds.test_number, who="the test number")

    def hangup(self) -> None:
        """The hold seam's handler: the panel's End button or a 2 s hold."""
        with self._lock:
            call = self._call
            broadcast = self._broadcast
        if call is None:
            return
        if broadcast is not None:
            broadcast({"type": "phone", "via": VIA, "action": "hangup", "id": call.id})
        self._end("she hung up")

    # -- internals ------------------------------------------------------

    def _view_locked(self) -> CallView | None:
        call = self._call
        if call is None:
            return None
        return CallView(call.who, call.number, call.status, call.connected_at)

    def _show(self, view: CallView | None) -> None:
        if self._panel is not None:
            self._panel(view)

    def _end(self, why: str) -> None:
        with self._lock:
            if self._call is None:
                return
            self._call = None
        logger.info("twilio call over: %s", why)
        if self._hold is not None:
            self._hold.clear()
        self._show(None)
