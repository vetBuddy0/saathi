"""Calls over the SIM of the phone or tablet the engine is running on.

Why it exists: inside the APK there is no Twilio relay (no cloudflared,
no echo-cancelled pair) and no family app (no Cryptodome, no public
URL), so "call my son" had nowhere to go. A device with a SIM already
makes phone calls, with its own echo cancelling and its own in-call
screen. The engine still decides *who* (`tools/calling.py`'s matching,
cards and permission check are unchanged -- this object stands where the
Twilio `CallController` stands); the shell only places the call it is
told to place. "The voice engine never executes anything": the dial
frame leaves from here, after the registry let the tool run.

The frame is `{"type": "phone", "action": "dial", "number": ...}` on
`/ws`. The face page ignores it; the Android shell waits until core.py
is idle -- so "Calling Udhi." is heard before the in-call screen takes
the audio -- and then places the call on speaker. The number is in the
frame and nowhere else: never in a tool result, never in a log line.

Readiness comes from the shell: on every `/ws` open it says whether it
can place calls (`{"type": "phone_status", "can_call": true}` -- a SIM,
the telephony feature). Until one connected client has said yes, the
route answers "unavailable" with a plain reason, so the model says so
instead of claiming a call it can't make. A client that goes away takes
its yes with it.

Lost: reporting the call's end back from the shell. That needs
READ_PHONE_STATE (another runtime prompt) and the in-call screen already
owns hang-up; `active` is instead a short debounce after each dial, so a
second "call him" while the first is still ringing out isn't a second
call. Also lost: dialling from Python through Chaquopy's Java bridge --
it would be the engine holding an Android handle, which is the shell's
job, and it would work only for the embedded engine, not for one on the
LAN.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# How long after a dial a new one is refused as "already in progress":
# the time the in-call screen takes to come up and ring.
DIAL_DEBOUNCE_SECONDS = 20.0
# Where "the test number" comes from: the same env var Twilio uses, so
# one keys page fills both.
TEST_NUMBER_VAR = "TWILIO_TEST_NUMBER"

NOT_CONNECTED = "This device can't place phone calls right now."


class DevicePhoneError(RuntimeError):
    """The shell can't take the dial (gone, or never said it can call)."""


class DevicePhone:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._broadcast: Callable[[dict], None] | None = None
        self._capable: set[Any] = set()
        self._last_dial: float | None = None

    def set_broadcast(self, fn: Callable[[dict], None] | None) -> None:
        with self._lock:
            self._broadcast = fn

    def on_status(self, client: Any, can_call: bool) -> None:
        """A `/ws` client's word on whether it can place calls; `False`
        also when that client disconnects."""
        with self._lock:
            if can_call:
                self._capable.add(client)
            else:
                self._capable.discard(client)

    def ready(self) -> str | None:
        """None when a connected shell can place calls, else why not --
        `tools/calling.py`'s `phone_ready`."""
        with self._lock:
            return None if self._capable and self._broadcast is not None else NOT_CONNECTED

    @property
    def active(self) -> bool:
        with self._lock:
            last = self._last_dial
        return last is not None and self._clock() - last < DIAL_DEBOUNCE_SECONDS

    def dial(self, number: str, who: str = "") -> None:
        with self._lock:
            broadcast = self._broadcast if self._capable else None
            if broadcast is None or not number:
                raise DevicePhoneError(NOT_CONNECTED)
            self._last_dial = self._clock()
        # Who, never the number (module docstring).
        logger.info("asking the shell to call %s", who or "a number")
        broadcast({"type": "phone", "action": "dial", "number": number})

    def dial_test_number(self) -> None:
        number = os.environ.get(TEST_NUMBER_VAR, "").strip()
        if not number:
            raise DevicePhoneError("No test number is set on this device.")
        self.dial(number, who="the test number")
