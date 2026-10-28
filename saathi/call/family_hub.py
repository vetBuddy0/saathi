"""Everything the family app does besides ringing: messages both ways,
call history, how she is today, and her reminders.

Why it exists: the owner asked for the family app to be "fully functional"
with more in it than calling (2026-10-20). A call needs her to pick up at
that moment; a typed message Melody reads aloud doesn't, and her spoken
reply arrives on their phone. The family also asked, through the
quiet-day and reminder work, "is she alright today?", and this answers it
when they open the app, not by pushing it at them.

Where the line is. The family sees *that* she talked to Melody today, and
when; whether she answered each reminder; and what the quiet-day check
did. They never see what she said to Melody. Lost: a transcript or a
summary of her day, which would make Melody something she is watched
through. Messages are the exception, because she sends them to them.

Lost: putting these on `FamilyCalls` (call/webrtc.py). That module is a
state machine with one lock whose every rule is about a call; messages
and reminders have no phase. They share the socket and the member, not
the state.

Storage: two tables in `family.sqlite3` beside `FamilyRegistry`'s (that
file's docstring says why it isn't IdentityStore). Reminders and "how she
is" are read from her identity database through the initiative modules
that write it, so there is one meaning of "answered".

Messages are read to her word for word ("A message from Udhi: ...") by
the screen server through `speak_first`, when she is free. "Read" on the
sender's phone means Melody read it aloud, not that she heard it, and the
app says "read to her".

Threading: requests arrive on the family server's loop (through its
executor), tools on the turn's executor thread, the reader on the screen
server's. Every call opens its own sqlite connection; live sends go
through each socket's own thread-safe `send`.
"""

from __future__ import annotations

import itertools
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from saathi.call.contacts import normalise_name, normalise_relation
from saathi.call.family import FamilyRegistry, Member
from saathi.call.push import PushError, PushSender, notice_payload

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 500
HISTORY = 50
EVERYONE = frozenset({"everyone", "everybody", "all", "family", "the family", "all of them"})
REQUESTS = frozenset({
    "status", "messages", "send_message", "reminders", "add_reminder", "cancel_reminder", "calls",
})

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        member_id TEXT NOT NULL,
        direction TEXT NOT NULL,  -- "to_her" | "from_her"
        text TEXT NOT NULL,
        created_at REAL NOT NULL,
        read_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS call_log (
        id INTEGER PRIMARY KEY,
        member_id TEXT NOT NULL,
        direction TEXT NOT NULL,  -- "out": she called; "in": they called her
        reason TEXT NOT NULL,
        seconds REAL,
        ended_at REAL NOT NULL
    )""",
)

Send = Callable[[dict], None]


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def when_label(ts: float, now: datetime | None = None) -> str:
    """"Today 9:42 AM", "Yesterday 8 PM", "Mon 3:05 PM", "12 Oct": her
    clock, which is the device's."""
    when = _local(ts)
    now = now or datetime.now().astimezone()
    hour = when.hour % 12 or 12
    minutes = "" if when.minute == 0 else f":{when.minute:02d}"
    clock = f"{hour}{minutes} {'AM' if when.hour < 12 else 'PM'}"
    days = (now.date() - when.date()).days
    if days == 0:
        return f"Today {clock}"
    if days == 1:
        return f"Yesterday {clock}"
    if 0 < days < 7:
        return f"{when:%a} {clock}"
    return f"{when.day} {when:%b}"


def call_outcome(direction: str, reason: str, seconds: float | None) -> str:
    if seconds is not None:
        return f"Talked {int(seconds) // 60}:{int(seconds) % 60:02d}"
    if direction == "out":  # she called them
        return {
            "no answer": "Missed",
            "hung up": "Missed",
            "declined": "You declined",
            "answered elsewhere": "Answered on another phone",
        }.get(reason, "Didn't connect")
    return {
        "no answer": "She didn't answer",
        "declined": "She declined",
        "busy": "She was busy",
        "hung up": "You hung up",
    }.get(reason, "Didn't connect")


@dataclass(frozen=True)
class Message:
    id: int
    member_id: str
    direction: str
    text: str
    created_at: float
    read_at: float | None

    def for_app(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from": "her" if self.direction == "from_her" else "you",
            "text": self.text,
            "at": when_label(self.created_at),
            "read": self.read_at is not None,
        }


def _message(row: sqlite3.Row) -> Message:
    return Message(row["id"], row["member_id"], row["direction"], row["text"],
                   row["created_at"], row["read_at"])


class FamilyHub:
    def __init__(
        self,
        registry: FamilyRegistry,
        family_db: Path,
        identity_db: Path | None,
        *,
        push: PushSender | None = None,
        public_url: Callable[[], str | None] = lambda: None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._registry = registry
        self._path = Path(family_db)
        self._identity_db = Path(identity_db) if identity_db is not None else None
        self._push = push
        self._public_url = public_url
        self._clock = clock
        self._lock = threading.Lock()
        self._live: dict[int, tuple[str, Send]] = {}
        self._ids = itertools.count(1)
        with self._connect() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self, fn):
        conn = self._connect()
        try:
            with conn:
                return fn(conn)
        finally:
            conn.close()

    # -- live sockets -----------------------------------------------------------

    def attach(self, member_id: str, send: Send) -> int:
        with self._lock:
            token = next(self._ids)
            self._live[token] = (member_id, send)
        return token

    def detach(self, token: int) -> None:
        with self._lock:
            self._live.pop(token, None)

    def _tell_live(self, member_id: str, message: dict) -> None:
        with self._lock:
            sends = [send for mid, send in self._live.values() if mid == member_id]
        for send in sends:
            try:
                send(message)
            except Exception:
                logger.exception("family app live update failed")

    def _url(self) -> str:
        base = self._public_url()
        return f"{base}/family/" if base else "/family/"

    # -- the app's requests -------------------------------------------------------

    def handles(self, payload: Any) -> bool:
        return isinstance(payload, dict) and payload.get("type") in REQUESTS

    def handle(self, member: Member, payload: dict) -> dict[str, Any]:
        """One request from a paired phone; the reply goes back to that
        socket. Blocking (sqlite): the family server runs it off its loop."""
        kind = payload.get("type")
        if kind == "status":
            return self.status()
        if kind == "messages":
            return {"type": "messages", "messages": [m.for_app() for m in self.thread(member.id)]}
        if kind == "send_message":
            return self.send_to_her(member, payload.get("text"))
        if kind == "calls":
            return {"type": "calls", "calls": self.calls(member.id)}
        if kind in ("reminders", "add_reminder", "cancel_reminder"):
            return self._reminders(kind, payload)
        return {"type": "error", "detail": f"unknown request {kind!r}"}

    def status(self) -> dict[str, Any]:
        from saathi.identity.store import IdentityStore
        from saathi.initiative.quiet_day import today_state
        from saathi.initiative.reminders import today_summary

        if self._identity_db is None:
            return {"type": "status", "last_talked": None, "reminders": [], "quiet_day": None}
        with IdentityStore(self._identity_db) as store:
            turns = store.read("turns")
        last = None
        if turns:
            latest = max(datetime.fromisoformat(row["ts"]) for row in turns)
            last = when_label(latest.timestamp())
        return {
            "type": "status",
            "last_talked": last,
            "reminders": today_summary(self._identity_db),
            "quiet_day": today_state(self._identity_db),
        }

    def _reminders(self, kind: str, payload: dict) -> dict[str, Any]:
        from saathi.initiative.reminders import make_reminder_tools

        if self._identity_db is None:
            return {"type": "error", "detail": "reminders aren't available on this device"}
        set_tool, list_tool, cancel_tool = make_reminder_tools(self._identity_db)
        result = None
        if kind == "add_reminder":
            result = set_tool.handler(
                text=payload.get("text") or "",
                at=payload.get("at"),
                day=payload.get("day"),
                repeat=payload.get("repeat"),
                tell_family=payload.get("tell_family") is True,
            )
        elif kind == "cancel_reminder":
            result = cancel_tool.handler(id=payload.get("id"))
        listing = list_tool.handler()
        reply: dict[str, Any] = {"type": "reminders", "reminders": listing["reminders"]}
        if result is not None and result.get("status") != "ok":
            reply["error"] = result.get("detail") or "That didn't work."
        return reply

    # -- messages ---------------------------------------------------------------

    def thread(self, member_id: str) -> list[Message]:
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM messages WHERE member_id = ? ORDER BY id DESC LIMIT ?",
                (member_id, HISTORY),
            ).fetchall()
        )
        return [_message(row) for row in reversed(rows)]

    def _insert(self, member_id: str, direction: str, text: str) -> Message:
        now = self._clock()
        row_id = self._run(
            lambda c: c.execute(
                "INSERT INTO messages (member_id, direction, text, created_at, read_at)"
                " VALUES (?, ?, ?, ?, NULL)",
                (member_id, direction, text, now),
            ).lastrowid
        )
        return Message(row_id, member_id, direction, text, now, None)

    @staticmethod
    def _clean(text: Any) -> str | None:
        if not isinstance(text, str):
            return None
        text = " ".join(text.split())
        return text[:MAX_MESSAGE_CHARS] if text else None

    def send_to_her(self, member: Member, text: Any) -> dict[str, Any]:
        clean = self._clean(text)
        if clean is None:
            return {"type": "error", "detail": "The message is empty."}
        message = self._insert(member.id, "to_her", clean)
        update = {"type": "message", "message": message.for_app()}
        self._tell_live(member.id, update)  # their other open phones too
        logger.info("message for her from %s", member.label)
        return update

    def next_for_her(self) -> tuple[int, str] | None:
        """The oldest message not yet read to her, as the line Melody
        says: their words, untouched, after who sent it."""
        row = self._run(
            lambda c: c.execute(
                "SELECT * FROM messages WHERE direction = 'to_her' AND read_at IS NULL"
                " ORDER BY id LIMIT 1"
            ).fetchone()
        )
        if row is None:
            return None
        member = self._registry.get(row["member_id"])
        who = member.name if member is not None else "your family"
        return row["id"], f"A message from {who}: {row['text']}"

    def mark_read(self, message_id: int) -> None:
        now = self._clock()
        row = self._run(
            lambda c: (
                c.execute(
                    "UPDATE messages SET read_at = ? WHERE id = ? AND read_at IS NULL",
                    (now, message_id),
                ),
                c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone(),
            )[1]
        )
        if row is not None:
            update = {"type": "message", "message": _message(row).for_app()}
            self._tell_live(row["member_id"], update)

    def card(self, message_id: int) -> dict[str, Any] | None:
        """One message as the device's screen shows it while Melody reads
        it aloud: who, their words, when. None if it is gone."""
        row = self._run(
            lambda c: c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        )
        if row is None:
            return None
        member = self._registry.get(row["member_id"])
        return {
            "kind": "message",
            "from": member.name if member is not None else "Your family",
            "text": row["text"],
            "at": when_label(row["created_at"]),
        }

    def recent(self, limit: int = HISTORY) -> list[dict[str, Any]]:
        """Every member's thread interleaved, newest first, for the
        device's own Messages tab. Hers too: she sees what she sent."""
        rows = self._run(
            lambda c: c.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
            .fetchall()
        )
        names = {m.id: m.name for m in self._registry.members()}
        return [
            {
                "id": row["id"],
                "who": names.get(row["member_id"], "Your family"),
                "from_her": row["direction"] == "from_her",
                "text": row["text"],
                "at": when_label(row["created_at"]),
                "read": row["read_at"] is not None,
            }
            for row in rows
        ]

    def unread_for_her(self) -> list[tuple[Message, str]]:
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM messages WHERE direction = 'to_her' AND read_at IS NULL ORDER BY id"
            ).fetchall()
        )
        out = []
        for row in rows:
            member = self._registry.get(row["member_id"])
            out.append((_message(row), member.name if member is not None else "your family"))
        return out

    def _resolve(self, to: Any) -> list[Member] | str:
        """Who "to" means, or a sentence saying who could be meant."""
        members = self._registry.members()
        names = ", ".join(m.name for m in members) or "nobody yet"
        if not isinstance(to, str) or not to.strip():
            return f"Say who to send it to: {names}."
        wanted = " ".join(to.lower().split())
        if wanted in EVERYONE:
            return members or "No one in her family has paired a phone yet."
        by_name = [m for m in members if normalise_name(m.name) == normalise_name(wanted)]
        if by_name:
            return by_name
        relation = normalise_relation(wanted)
        by_relation = [m for m in members if relation is not None and m.relation == relation]
        if by_relation:
            return by_relation
        return f"No one called {to!r} has the family app. Paired: {names}."

    def from_her(self, to: Any, text: Any) -> dict[str, Any]:
        """The `message_family` tool: her words to their phones."""
        clean = self._clean(text)
        if clean is None:
            return {"status": "error", "detail": "the message is empty"}
        found = self._resolve(to)
        if isinstance(found, str):
            return {"status": "error", "detail": found}
        sent_to = []
        for member in found:
            message = self._insert(member.id, "from_her", clean)
            self._tell_live(member.id, {"type": "message", "message": message.for_app()})
            if member.subscription is not None and self._push is not None:
                payload = notice_payload(
                    f"msg-{message.id}",
                    f"Message from {member.calls_her or 'her'}",
                    clean,
                    self._url(),
                )
                try:
                    self._push.send(member.subscription, payload, ttl=24 * 3600, urgency="normal")
                except PushError as exc:
                    logger.warning("message push to %s failed: %s", member.label, exc)
            sent_to.append(member.name)
        return {"status": "ok", "sent_to": sent_to}

    def read_to_her_now(self) -> dict[str, Any]:
        """The `read_messages` tool: unread messages, which count as read
        once handed to the model to read out."""
        unread = self.unread_for_her()
        for message, _who in unread:
            self.mark_read(message.id)
        return {
            "status": "ok",
            "messages": [{"from": who, "text": m.text, "sent": when_label(m.created_at)}
                         for m, who in unread],
        }

    # -- calls --------------------------------------------------------------------

    def record_call(self, member: Member, direction: str, reason: str,
                    seconds: float | None) -> None:
        now = self._clock()
        self._run(
            lambda c: c.execute(
                "INSERT INTO call_log (member_id, direction, reason, seconds, ended_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (member.id, direction, reason, seconds, now),
            )
        )
        self._tell_live(member.id, {"type": "calls", "calls": self.calls(member.id)})

    def calls(self, member_id: str) -> list[dict[str, Any]]:
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM call_log WHERE member_id = ? ORDER BY id DESC LIMIT ?",
                (member_id, HISTORY),
            ).fetchall()
        )
        return [
            {
                "who": "She called" if row["direction"] == "out" else "You called",
                "outcome": call_outcome(row["direction"], row["reason"], row["seconds"]),
                "at": when_label(row["ended_at"]),
            }
            for row in rows
        ]


# -- her side: tools ----------------------------------------------------------------

MESSAGE_FAMILY_DESCRIPTION = (
    "Send a message from her to someone in her family who has the Melody app on "
    "their phone, when she asks to tell or message them (e.g. 'tell Udhi I'm "
    "fine'). to: their name, their relation ('my son'), or 'everyone'. text: what "
    "she wants to say, in her own words, first person, not reworded. After it's "
    "sent, say who it went to."
)
READ_MESSAGES_DESCRIPTION = (
    "Messages her family sent her that haven't been read to her yet. Use when she "
    "asks whether she has messages. Read each one to her word for word, saying who "
    "it's from."
)


def make_message_tools(hub: Callable[[], FamilyHub | None]):
    """`hub` is read late: the family app may still be coming up."""
    from saathi.tools.registry import Tool

    def _send(to: Any = None, text: Any = None) -> dict[str, Any]:
        current = hub()
        if current is None:
            return {"status": "unavailable", "detail": "the family app isn't on"}
        return current.from_her(to, text)

    def _read() -> dict[str, Any]:
        current = hub()
        if current is None:
            return {"status": "unavailable", "detail": "the family app isn't on"}
        return current.read_to_her_now()

    send_tool = Tool(
        name="message_family",
        schema={
            "type": "object",
            "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
            "required": ["to", "text"],
        },
        permission="messages",
        handler=_send,
    )
    read_tool = Tool(
        name="read_messages",
        schema={"type": "object", "properties": {}},
        permission="messages",
        handler=_read,
    )
    return send_tool, read_tool
