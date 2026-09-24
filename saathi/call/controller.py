"""One call at a time: dial, bridge audio when Twilio's stream opens,
hang up on a two-second hold, and know whether a call is happening.

Exists as the single owner of call state so the pieces around it stay
dumb: `twilio.py` only speaks REST, `media.py` only speaks the
WebSocket, `audio.py` only moves bytes, `relay.py` only exposes a URL.
The controller is the only thing that knows a `CallSid` belongs to a
`streamSid`, that a hold means "complete this call", or that the mic
should be released when the far end hangs up first.

Contested: no new `core.py` state. SPEC.md's `HANDOFF` is "a question
goes to the slower, smarter path" and resolves into the same turn — it
is not "on a call", and re-purposing it would lie to the face. A real
`IN_CALL` state is a `core.py` + `Face` change, proposed in
`docs/completed/calling.md`, not built here. Until it lands, `active`
is the signal: `initiative/policy.py`'s "a call is happening" (SPEC.md,
"the gate reads ... whether something else is happening — television, a
call") should read it.

Threading, because three threads meet here: `dial()` runs on the screen
server's executor thread (tool handlers do); `stream_started`/
`inbound_audio`/`stream_stopped` run on the media server's loop thread;
`hangup()` runs on whichever thread the hold seam fires from. One lock
around state; audio callbacks never take it.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable, Protocol

from saathi.call.audio import CallAudio
from saathi.call.hangup import HANGUP_HOLD_SECONDS, HANGUP_LABEL, HoldSeam
from saathi.call.relay import Relay
from saathi.call.twilio import TwilioClient, TwilioCredentials, TwilioError, sanitize

logger = logging.getLogger(__name__)


class CallState(str, Enum):
    IDLE = "idle"
    DIALLING = "dialling"  # REST accepted the call; no stream yet
    IN_CALL = "in_call"  # Twilio's stream is open; audio is bridged


class ServerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class CallController:
    def __init__(
        self,
        credentials: TwilioCredentials,
        client: TwilioClient,
        relay: Relay,
        server: ServerLike,
        audio_factory: Callable[[], CallAudio],
        hold: HoldSeam,
    ) -> None:
        self._credentials = credentials
        self._client = client
        self._relay = relay
        self._server = server
        self._audio_factory = audio_factory
        self._hold = hold
        self._lock = threading.Lock()
        self._state = CallState.IDLE
        self._call_sid: str | None = None
        self._stream_sid: str | None = None
        self._audio: CallAudio | None = None
        self._prepared = False
        self.on_state_change: Callable[[CallState], None] | None = None

    # -- observation -------------------------------------------------------

    @property
    def state(self) -> CallState:
        return self._state

    @property
    def active(self) -> bool:
        """True from dial until the call is over. The "a call is
        happening" signal for initiative's gate and, once it exists, the
        `IN_CALL` core state."""
        return self._state is not CallState.IDLE

    @property
    def call_sid(self) -> str | None:
        return self._call_sid

    @property
    def audio(self) -> CallAudio | None:
        return self._audio

    def _set_state(self, state: CallState) -> None:
        self._state = state
        if self.on_state_change is not None:
            self.on_state_change(state)

    # -- URLs --------------------------------------------------------------

    def ws_url(self) -> str:
        public = self._relay.public_url
        if public.startswith("https://"):
            public = "wss://" + public[len("https://") :]
        return public.rstrip("/") + "/media"

    def _twiml_url(self) -> str:
        return self._relay.public_url.rstrip("/") + "/twiml"

    # -- lifecycle ---------------------------------------------------------

    def prepare(self) -> None:
        """Bring the relay and the local server up. Called at boot by
        `cli.py` so the first dial doesn't wait on a tunnel; also called
        by `dial()` itself if nothing did."""
        if self._prepared:
            return
        self._server.start()
        self._relay.start()
        self._prepared = True

    def shutdown(self) -> None:
        if self.active:
            self.hangup(wait=True)
        if self._prepared:
            self._relay.stop()
            self._server.stop()
            self._prepared = False

    # -- dialling ----------------------------------------------------------

    def dial(self, to_number: str) -> str:
        """Places the call; returns the CallSid. Raises `TwilioError`
        (sanitized) on any failure, including from an injected client
        that puts a URL or a number in its message."""
        with self._lock:
            if self._state is not CallState.IDLE:
                raise TwilioError("a call is already in progress")
            self.prepare()
            try:
                sid = self._client.create_call(
                    to_number, self._credentials.from_number, self._twiml_url()
                )
            except BaseException as exc:
                raise sanitize("create call", exc) from None
            self._call_sid = sid
            self._set_state(CallState.DIALLING)
        self._hold.set_handler(self.hangup, seconds=HANGUP_HOLD_SECONDS, label=HANGUP_LABEL)
        logger.info("call placed")
        return sid

    def dial_test_number(self) -> str:
        return self.dial(self._credentials.test_number)

    # -- StreamHandler (media server's loop thread) --------------------------

    def stream_started(
        self, stream_sid: str, call_sid: str, send_outbound: Callable[[bytes], None]
    ) -> None:
        with self._lock:
            if self._state is CallState.IDLE:
                # A stream for a call we didn't place, or one that was
                # already hung up: refuse to bridge the mic to it.
                logger.warning("stream opened with no call in progress; ignoring")
                return
            if self._audio is not None:
                self._audio.stop()
            self._stream_sid = stream_sid
            self._audio = self._audio_factory()
            self._audio.start(send_outbound)
            self._set_state(CallState.IN_CALL)
        logger.info("call stream open")

    def inbound_audio(self, ulaw: bytes) -> None:
        audio = self._audio
        if audio is not None:
            audio.feed_inbound(ulaw)

    def stream_stopped(self, stream_sid: str) -> None:
        with self._lock:
            if stream_sid != self._stream_sid:
                return
            self._teardown_locked()
        logger.info("call stream closed")

    # -- hang-up (the hold seam) -----------------------------------------------

    def hangup(self, wait: bool = False) -> threading.Thread | None:
        """Tears down at once (mic and sink released, state IDLE), then
        tells Twilio on a worker thread: the hold seam fires this on the
        screen server's event loop, which has a 100 ms face budget and
        must not block on an HTTP request. `wait=True` joins it (tests,
        the live script, `shutdown()`)."""
        with self._lock:
            sid = self._call_sid
            if sid is None:
                return None
            self._teardown_locked()
        worker = threading.Thread(target=self._complete, args=(sid,), daemon=True)
        worker.start()
        if wait:
            worker.join(timeout=20.0)
        logger.info("call ended")
        return worker

    def _complete(self, sid: str) -> None:
        try:
            self._client.complete_call(sid)
        except BaseException as exc:
            # The stream is already torn down and the mic released; a
            # failed REST hang-up is logged (sanitized) and the far end's
            # own hang-up or Twilio's socket close finishes the job.
            logger.warning("%s", sanitize("complete call", exc))

    def _teardown_locked(self) -> None:
        audio, self._audio = self._audio, None
        self._stream_sid = None
        self._call_sid = None
        if audio is not None:
            audio.stop()
        self._hold.clear()
        self._set_state(CallState.IDLE)
