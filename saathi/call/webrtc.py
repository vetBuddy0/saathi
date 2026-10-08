"""Calls to the family app over the internet: the signaling state machine.

Exists because phoning family through Twilio costs per minute, and an
international minute is what this product's users would make most
(DECISIONS 2026-10-07). A WebRTC call between two browsers costs
nothing per minute: the kiosk's Chromium (the face page) is one end,
the family member's phone (the PWA in `screen/static/family/`) is the
other, and this module is the only thing between them -- it decides who
may talk to whom, about which call, and when the call is over. Media
never passes through Python; only the offer/answer and ICE candidates
do, and only between the two parties of the current call.

The device end is the face page, not a native WebRTC stack in this
process (aiortc lost: a large native dependency whose aarch64 builds
need libav/libvpx, plus a second audio path to keep echo-free). Chromium
already has WebRTC, its own echo canceller -- which, unlike
`audio/aec.py`'s module, has the far end's audio as its reference
because Chromium is what plays it -- and it already opens the system
default microphone and speaker, so no device is ever named.

One call at a time, across both calling paths: `other_active` is the
Twilio controller's `active`, so a family call and a phone call can't
overlap. Phases:

    IDLE -> RINGING_OUT -> CONNECTING -> CONNECTED -> IDLE   (she calls them)
    IDLE -> RINGING_IN  -> CONNECTING -> CONNECTED -> IDLE   (they call her)

Rules that are the point of this module, each covered by a test:
- **Never auto-answered.** RINGING_IN leaves only by her action: a tap
  on the card, a held spacebar, or her "yes" reaching `answer_card` --
  or by the ring timing out, the caller giving up, or her "no".
- **Only the parties of the current call exchange anything.** A
  signal with the wrong call id, from a member who isn't bound to the
  call, or from a second screen that lost the race to be the device end,
  is dropped.
- **The device is always the offerer**, in both directions. One code
  path for the hard part (the kiosk's offer), and the family page only
  ever answers.
- **Hang-up is the hold seam's handler**, exactly as for Twilio calls:
  a two-second spacebar hold and the phone panel's End button reach the
  same `hangup()`.

Contested: answering by spacebar is a hold of `ANSWER_HOLD_SECONDS`
(0.2 s) through the same `HoldController`, not a new "tap" seam. A
press during a ring must not start a turn (it would talk over the
ring), which is exactly what the hold seam already guarantees; a new
seam would be a second way for the spacebar to mean something other
than "talk". A very short tap (< 0.1 s, one server tick) is abandoned
and does nothing; the card she can see is re-shown on the next tick.

Contested: incoming calls ask with a Confirm card ("Priya is calling.
Answer?"), not the phone panel. The panel's one button fires the hold
handler, which during a ring is *answer* -- an End button that answers
would be a trap. The panel appears once she has answered.

Threading: tool handlers (executor thread), the family server's loop,
the screen server's loop (device messages, taps, hold) and the ticker
thread all call in. One lock around state; everything with an effect
outside this object -- a send, a card, the panel, the hold seam, a push
-- is collected under the lock and run after it is released, because
the card controller calls back into `_on_card_answer` synchronously.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from saathi.call.family import FamilyRegistry, Member
from saathi.call.hangup import HANGUP_HOLD_SECONDS, HANGUP_LABEL
from saathi.call.push import PushError, PushSender, cancel_payload, ring_payload

logger = logging.getLogger(__name__)

RING_TIMEOUT_SECONDS = 45.0
# ICE that hasn't connected in this long won't: almost always a NAT that
# needs TURN (docs/DEMO.md, "TURN").
CONNECT_TIMEOUT_SECONDS = 30.0
ANSWER_HOLD_SECONDS = 0.2
ANSWER_LABEL = "Answering"
TICK_SECONDS = 0.5
MISSED_REASONS = frozenset({"no answer", "hung up"})

Send = Callable[[dict], None]


class Phase(str, Enum):
    IDLE = "idle"
    RINGING_OUT = "ringing_out"  # she is calling them; their phone rings
    RINGING_IN = "ringing_in"  # they are calling her; the device rings
    CONNECTING = "connecting"  # answered; offer/answer/ICE in flight
    CONNECTED = "connected"  # the kiosk reported ICE connected


class MemberConn:
    """One open family-app socket. `send` must be thread-safe (the
    family server hops to its own loop). `call_id` set means the socket
    authenticated with a per-call token from a push notification: it
    may answer or decline that call and nothing else."""

    def __init__(self, member: Member, send: Send, call_id: str | None = None) -> None:
        self.member = member
        self.send = send
        self.call_id = call_id


@dataclass
class _Call:
    id: str
    member: Member
    direction: str  # "out" | "in"
    phase: Phase
    phase_at: float
    token: str  # lets a page from a push link answer this call only
    conn: MemberConn | None = None
    device_peer: str | None = None
    connected_at: float | None = None
    card_id: str | None = None
    ever_answered: bool = False


@dataclass
class _Effects:
    actions: list[Callable[[], None]] = field(default_factory=list)

    def add(self, fn: Callable[[], None]) -> None:
        self.actions.append(fn)

    def run(self) -> None:
        for action in self.actions:
            try:
                action()
            except Exception:
                logger.exception("family call side effect failed")


class FamilyCalls:
    def __init__(
        self,
        registry: FamilyRegistry,
        *,
        push: PushSender | None,
        public_url: Callable[[], str | None],
        hold: Any,
        cards: Any = None,
        panel: Callable[[Any], None] | None = None,
        emotions: Any = None,
        ice_servers: Callable[[], list[dict]] = lambda: [],
        other_active: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
        ring_timeout: float = RING_TIMEOUT_SECONDS,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._registry = registry
        self._push = push
        self._public_url = public_url
        self._hold = hold
        self._cards = cards
        self._panel = panel
        self._emotions = emotions
        self._ice_servers = ice_servers
        self._other_active = other_active
        self._clock = clock
        self._ring_timeout = ring_timeout
        self._connect_timeout = connect_timeout
        self._lock = threading.RLock()
        self._call: _Call | None = None
        self._conns: list[MemberConn] = []
        self._device_send: Send | None = None
        self._voice_outcomes: dict[str, dict[str, Any]] = {}
        self._ticker: threading.Thread | None = None
        self._ticker_stop = threading.Event()
        if cards is not None:
            cards.on_answer(self._on_card_answer)

    # -- observation ---------------------------------------------------------

    @property
    def phase(self) -> Phase:
        call = self._call
        return call.phase if call is not None else Phase.IDLE

    @property
    def active(self) -> bool:
        return self._call is not None

    @property
    def call_id(self) -> str | None:
        call = self._call
        return call.id if call is not None else None

    def paired(self, name: str) -> Member | None:
        return self._registry.find_by_name(name)

    # -- the screen seam -------------------------------------------------------

    def set_broadcast(self, send: Send | None) -> None:
        """The screen server's thread-safe broadcast, like cards and
        media. Device messages are `{"type": "rtc", ...}`."""
        self._device_send = send

    def _to_device(self, fx: _Effects, message: dict) -> None:
        payload = {"type": "rtc", **message}
        send = self._device_send
        if send is not None:
            fx.add(lambda: send(payload))

    def _to_member(self, fx: _Effects, conn: MemberConn | None, message: dict) -> None:
        if conn is not None:
            fx.add(lambda: conn.send(message))

    def _to_all_of(self, fx: _Effects, member_id: str, message: dict, skip=None) -> None:
        for conn in list(self._conns):
            if conn.member.id == member_id and conn is not skip:
                self._to_member(fx, conn, message)

    def _set_panel(self, fx: _Effects, call: _Call | None, status: str | None = None) -> None:
        if self._panel is None:
            return
        if call is None or status is None:
            fx.add(lambda: self._panel(None))
            return
        from saathi.screen.call_panel import CallView

        view = CallView(
            name=call.member.name, number="", status=status, connected_at=call.connected_at
        )
        fx.add(lambda: self._panel(view))

    def _emotion(self, fx: _Effects, name: str, seconds: float | None = None) -> None:
        if self._emotions is not None:
            fx.add(lambda: self._emotions.show(name, seconds))

    def _start_messages(self, fx: _Effects, call: _Call) -> None:
        ice = self._ice_servers()
        self._to_device(
            fx, {"action": "start", "call_id": call.id, "role": "offerer", "ice_servers": ice}
        )
        self._to_member(
            fx,
            call.conn,
            {"type": "start", "call_id": call.id, "role": "answerer", "ice_servers": ice},
        )

    # -- she calls them ----------------------------------------------------------

    def dial(self, member: Member) -> dict[str, Any]:
        """Rings `member`'s phone. Returns a tool result (`status` +
        `note`), the same shapes `tools/calling.py`'s `_dial` returns,
        so the model is steered the same way whichever path rang."""
        fx = _Effects()
        with self._lock:
            if self._call is not None or self._other_active():
                return {
                    "status": "busy",
                    "note": "A call is already in progress. Say so in one short sentence.",
                }
            live = [c for c in self._conns if c.member.id == member.id and c.call_id is None]
            if member.subscription is None and not live:
                return _unreachable(member.name)
            call = _Call(
                id=secrets.token_hex(8),
                member=member,
                direction="out",
                phase=Phase.RINGING_OUT,
                phase_at=self._clock(),
                token=secrets.token_urlsafe(24),
            )
            self._call = call
            self._set_panel(fx, call, "calling")
            fx.add(
                lambda: self._hold.set_handler(
                    self.hangup, seconds=HANGUP_HOLD_SECONDS, label=HANGUP_LABEL
                )
            )
            self._to_all_of(fx, member.id, self._ringing_message(call))
        fx.run()
        self._ensure_ticker()
        delivered = bool(live)
        if member.subscription is not None and self._push is not None:
            try:
                self._push.send(member.subscription, self._ring_payload(call))
                delivered = True
            except PushError as exc:
                logger.warning("ring push to %s failed: %s", member.label, exc)
                if exc.gone:
                    self._registry.set_subscription(member.id, None)
        if not delivered:
            self._end(call.id, "unreachable", push_cancel=False)
            return _unreachable(member.name)
        fx = _Effects()
        with self._lock:
            if self._call is call and call.phase is Phase.RINGING_OUT:
                self._set_panel(fx, call, "ringing")
        fx.run()
        logger.info("family call to %s ringing", member.label)
        return {
            "status": "calling",
            "note": (
                f"The call to {member.name} is ringing on their phone right now. "
                f"Reply with exactly 'Calling {member.name}.' and nothing else."
            ),
        }

    def _ringing_message(self, call: _Call) -> dict:
        return {"type": "ringing", "call_id": call.id, "direction": "in"}

    def _ring_payload(self, call: _Call) -> dict:
        base = (self._public_url() or "").rstrip("/")
        url = f"{base}/family/#call={call.id}&t={call.token}&m={call.member.id}"
        return ring_payload(call.id, _her_name(call.member), url)

    # -- they call her ---------------------------------------------------------

    def attach(self, member: Member, send: Send, call_id: str | None = None) -> MemberConn:
        """A family-app socket authenticated as `member`. Tells it about
        a call already ringing for it (opened from the notification)."""
        conn = MemberConn(member, send, call_id)
        fx = _Effects()
        with self._lock:
            self._conns.append(conn)
            call = self._call
            if (
                call is not None
                and call.member.id == member.id
                and call.direction == "out"
                and call.phase is Phase.RINGING_OUT
                and (call_id is None or call_id == call.id)
            ):
                self._to_member(fx, conn, self._ringing_message(call))
        fx.run()
        return conn

    def attach_with_call_token(self, call_id: Any, token: Any, send: Send) -> MemberConn | None:
        """A page opened from a push link on an origin that has no stored
        key (a quick tunnel's hostname changed since pairing)."""
        with self._lock:
            call = self._call
            if (
                call is None
                or call.direction != "out"
                or call.id != call_id
                or not isinstance(token, str)
                or not secrets.compare_digest(call.token, token)
            ):
                return None
            member = call.member
        return self.attach(member, send, call_id=call.id)

    def decline_with_token(self, call_id: Any, token: Any) -> bool:
        """The notification's Decline action: the service worker has no
        member key, only the per-call token its push carried."""
        with self._lock:
            call = self._call
            if (
                call is None
                or call.direction != "out"
                or call.phase is not Phase.RINGING_OUT
                or call.id != call_id
                or not isinstance(token, str)
                or not secrets.compare_digest(call.token, token)
            ):
                return False
        self._end(call.id, "declined")
        return True

    def detach(self, conn: MemberConn) -> None:
        with self._lock:
            if conn in self._conns:
                self._conns.remove(conn)
            call = self._call
            bound = call is not None and call.conn is conn
        if bound:
            self._end(call.id, "the other side left")

    def on_member_message(self, conn: MemberConn, message: Any) -> None:
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        call_id = message.get("call_id")
        if kind == "call":
            self._incoming(conn)
            return
        fx = _Effects()
        end_reason: str | None = None
        with self._lock:
            call = self._call
            if call is None or call_id != call.id or call.member.id != conn.member.id:
                return
            if conn.call_id is not None and conn.call_id != call.id:
                return
            if kind == "accept" and call.direction == "out" and call.phase is Phase.RINGING_OUT:
                call.conn = conn
                call.ever_answered = True
                self._enter(call, Phase.CONNECTING)
                self._set_panel(fx, call, "connected")
                self._start_messages(fx, call)
                self._to_all_of(
                    fx,
                    call.member.id,
                    {"type": "ended", "call_id": call.id, "reason": "answered elsewhere"},
                    skip=conn,
                )
            elif kind == "decline" and call.phase is Phase.RINGING_OUT:
                end_reason = "declined"
            elif kind == "end" and (call.conn is conn or call.phase is Phase.RINGING_OUT):
                end_reason = "ended by them"
            elif kind == "signal" and call.conn is conn and call.device_peer is not None:
                data = message.get("data")
                if isinstance(data, dict):
                    self._to_device(
                        fx,
                        {
                            "action": "signal",
                            "call_id": call.id,
                            "to": call.device_peer,
                            "data": data,
                        },
                    )
        fx.run()
        if end_reason is not None:
            self._end(call.id, end_reason)

    def _incoming(self, conn: MemberConn) -> None:
        fx = _Effects()
        with self._lock:
            if conn.call_id is not None:
                return  # a push-link page may answer, never start a call
            if self._call is not None or self._other_active():
                self._to_member(fx, conn, {"type": "ended", "call_id": None, "reason": "busy"})
                fx.run()
                return
            call = _Call(
                id=secrets.token_hex(8),
                member=conn.member,
                direction="in",
                phase=Phase.RINGING_IN,
                phase_at=self._clock(),
                token="",
                conn=conn,
            )
            self._call = call
            self._to_member(fx, conn, {"type": "waiting", "call_id": call.id})
            self._to_device(fx, {"action": "ring", "call_id": call.id, "name": call.member.name})
            self._emotion(fx, "surprised", 3.0)
            fx.add(lambda: self._show_incoming_card(call))
            fx.add(
                lambda: self._hold.set_handler(
                    self.answer, seconds=ANSWER_HOLD_SECONDS, label=ANSWER_LABEL
                )
            )
        fx.run()
        self._ensure_ticker()
        logger.info("family call from %s ringing", conn.member.label)

    def _show_incoming_card(self, call: _Call) -> None:
        if self._cards is None:
            return
        from saathi.screen.cards import confirm

        name = call.member.name
        card = confirm(f"{name} is calling. Answer?", spoken=f"{name} is calling. Answer?")
        with self._lock:
            if self._call is not call or call.phase is not Phase.RINGING_IN:
                return
            call.card_id = card.id
        self._cards.show(card)

    def answer(self) -> bool:
        """Her action, and only hers: the hold seam, the card's Yes, or
        her "yes" through `answer_card`. True if a ringing call was
        answered."""
        fx = _Effects()
        with self._lock:
            call = self._call
            if call is None or call.direction != "in" or call.phase is not Phase.RINGING_IN:
                return False
            call.ever_answered = True
            self._enter(call, Phase.CONNECTING)
            self._clear_card(fx, call)
            fx.add(
                lambda: self._hold.set_handler(
                    self.hangup, seconds=HANGUP_HOLD_SECONDS, label=HANGUP_LABEL
                )
            )
            self._set_panel(fx, call, "connected")
            self._to_device(fx, {"action": "ring_stop", "call_id": call.id})
            self._start_messages(fx, call)
        fx.run()
        logger.info("family call answered")
        return True

    def decline(self) -> bool:
        with self._lock:
            call = self._call
            if call is None or call.direction != "in" or call.phase is not Phase.RINGING_IN:
                return False
        self._end(call.id, "declined")
        return True

    def _clear_card(self, fx: _Effects, call: _Call) -> None:
        card_id, call.card_id = call.card_id, None
        cards = self._cards
        if cards is None or card_id is None:
            return

        def clear() -> None:
            current = cards.current
            if current is not None and current.id == card_id:
                cards.clear()

        fx.add(clear)

    # -- cards (her tap or her voice) -------------------------------------------

    def _on_card_answer(self, answer: Any) -> None:
        with self._lock:
            call = self._call
            if call is None or call.card_id != answer.card_id:
                return
            call.card_id = None  # that card is gone either way
        if answer.source == "code":
            # Replaced, not answered: the hold's own card took its place,
            # or something else was shown. The ticker re-shows it.
            return
        if answer.yes:
            answered = self.answer()
            result = {"status": "answered", "say": ""} if answered else None
        else:
            self.decline()
            result = {
                "status": "declined",
                "note": f"She chose not to answer {call.member.name}'s call. Say nothing more "
                "than a short, warm acknowledgement.",
            }
        if answer.source == "voice" and result is not None:
            with self._lock:
                self._voice_outcomes[answer.card_id] = result

    def pending_card_ids(self) -> set[str]:
        """`answer_card`'s flow protocol (`tools/calling.py`)."""
        with self._lock:
            call = self._call
            return {call.card_id} if call is not None and call.card_id else set()

    def outcome(self, card_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._voice_outcomes.pop(card_id, None)

    # -- the device (kiosk) ------------------------------------------------------

    def on_device_message(self, message: Any) -> None:
        """`{"type": "rtc", "action": ..., "call_id": ..., "peer": ...}`
        from the screen server's socket."""
        if not isinstance(message, dict):
            return
        action = message.get("action")
        peer = message.get("peer")
        if not isinstance(peer, str) or not peer:
            return
        fx = _Effects()
        end_reason: str | None = None
        with self._lock:
            call = self._call
            if call is None or message.get("call_id") != call.id:
                return
            if call.phase not in (Phase.CONNECTING, Phase.CONNECTED):
                return
            if action == "ready":
                if call.device_peer is None:
                    call.device_peer = peer
                elif call.device_peer != peer:
                    # A second screen also answered `start`: it stands down.
                    self._to_device(fx, {"action": "end", "call_id": call.id, "to": peer})
            elif peer != call.device_peer:
                return
            elif action == "signal":
                data = message.get("data")
                if isinstance(data, dict):
                    self._to_member(
                        fx, call.conn, {"type": "signal", "call_id": call.id, "data": data}
                    )
            elif action == "connected" and call.phase is Phase.CONNECTING:
                self._enter(call, Phase.CONNECTED)
                call.connected_at = self._clock()
                self._set_panel(fx, call, "connected")
                self._emotion(fx, "happy", 3.0)
                self._to_member(fx, call.conn, {"type": "connected", "call_id": call.id})
            elif action == "failed":
                reason = message.get("reason")
                end_reason = f"device: {reason}" if isinstance(reason, str) else "device failed"
        fx.run()
        if end_reason is not None:
            logger.warning("family call failed on the device: %s", end_reason)
            self._end(call.id, end_reason)

    # -- ending ----------------------------------------------------------------

    def hangup(self) -> None:
        """The hold seam's handler: a two-second hold or the panel's End
        button. Never blocks (the push for a missed ring goes on a
        worker thread)."""
        call = self._call
        if call is not None:
            self._end(call.id, "hung up")

    def _enter(self, call: _Call, phase: Phase) -> None:
        call.phase = phase
        call.phase_at = self._clock()

    def _end(self, call_id: str, reason: str, *, push_cancel: bool = True) -> None:
        fx = _Effects()
        with self._lock:
            call = self._call
            if call is None or call.id != call_id:
                return
            self._call = None
            self._to_device(fx, {"action": "end", "call_id": call.id})
            ended = {"type": "ended", "call_id": call.id, "reason": reason}
            self._to_all_of(fx, call.member.id, ended)
            if call.conn is not None and call.conn not in self._conns:
                self._to_member(
                    fx, call.conn, {"type": "ended", "call_id": call.id, "reason": reason}
                )
            self._clear_card(fx, call)
            self._set_panel(fx, None)
            fx.add(self._hold.clear)
            # Only a ring they never picked up becomes "Missed call" on
            # their phone; a decline or their own hang-up needs nothing.
            notify_phone = (
                push_cancel
                and reason in MISSED_REASONS
                and call.direction == "out"
                and not call.ever_answered
                and call.member.subscription is not None
                and self._push is not None
            )
        fx.run()
        logger.info("family call ended: %s", reason)
        if notify_phone:
            payload = cancel_payload(call.id, _her_name(call.member), missed=True)
            threading.Thread(
                target=self._push_quietly, args=(call.member, payload), daemon=True
            ).start()

    def _push_quietly(self, member: Member, payload: dict) -> None:
        try:
            assert self._push is not None and member.subscription is not None
            self._push.send(member.subscription, payload, ttl=6 * 3600, urgency="normal")
        except PushError as exc:
            logger.info("cancel push not delivered: %s", exc)

    # -- time ------------------------------------------------------------------

    def tick(self) -> None:
        """Timeouts, and the incoming card's re-show. Run by the ticker
        thread every `TICK_SECONDS`; tests call it with a fake clock."""
        reshow = None
        with self._lock:
            call = self._call
            if call is None:
                return
            age = self._clock() - call.phase_at
            if call.phase in (Phase.RINGING_OUT, Phase.RINGING_IN) and age >= self._ring_timeout:
                reason = "no answer"
            elif call.phase is Phase.CONNECTING and age >= self._connect_timeout:
                reason = "could not connect"
            else:
                reason = None
                if (
                    call.phase is Phase.RINGING_IN
                    and call.card_id is None
                    and self._cards is not None
                    and self._cards.current is None
                    and not getattr(self._hold, "holding", False)
                ):
                    reshow = call
        if reason is not None:
            if reason == "could not connect":
                logger.warning(
                    "family call never connected; a TURN server is probably needed "
                    "(SAATHI_ICE_SERVERS / SAATHI_TURN_*, docs/DEMO.md)"
                )
            self._end(call.id, reason)
        elif reshow is not None:
            self._show_incoming_card(reshow)

    def _ensure_ticker(self) -> None:
        with self._lock:
            if self._ticker is not None and self._ticker.is_alive():
                return
            self._ticker_stop.clear()
            self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
            self._ticker.start()

    def _tick_loop(self) -> None:
        while not self._ticker_stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                logger.exception("family call tick failed")
            if self._call is None:
                return

    def shutdown(self) -> None:
        self.hangup()
        self._ticker_stop.set()


def _her_name(member: Member) -> str:
    """What the family member calls her ("Mum"), from their pairing; a
    neutral fallback otherwise."""
    return getattr(member, "calls_her", None) or "Kaki"


def _unreachable(name: str) -> dict[str, Any]:
    return {
        "status": "error",
        "note": (
            f"{name}'s phone can't be reached through the family app just now (it has "
            "not allowed notifications, or isn't connected). Say so plainly in one short "
            "sentence and suggest trying again later."
        ),
    }
