"""The family's messages and reminders, fetched from the cloud; what
happened to her reminders, sent back.

Why it exists: the owner decided (2026-10-29) that caregivers use an
app on Firebase to send her messages, which Saathi reads aloud, and to
set her reminders. This is the device's half: `check()` is called every
few seconds by the screen server (`build_app(inbox=...)`, on the
executor -- every call here is the network), and it

- hands back the messages not yet said, oldest first, each as the line
  she hears: "Message from {from}: {text}", word for word. SPEC.md:
  phrasing may not invent. Lost: passing the message through the model
  to "say it warmly" -- a model that turns "Running late, home by 8" into
  "Priya will be home by 8" has made a promise for Priya; a template
  with their words in it cannot.
- copies each caregiver reminder into her local `reminders` table, so
  the existing reminder lane says it, repeats it and records it, offline
  too. `IdentityStore` has no update: an edit retires the old row and
  appends a new one; `active=false` retires it. Which local row stands
  for which remote one, at which `updatedAt`, is `cloud_links`. Written
  through the store as the console writes them, not through the
  `set_reminder` Tool: a Tool's permission gates what the model may ask
  for, and a caregiver's reminder is not the model's request.
- sends each `fired`/`acked`/`missed` from `reminder_events` to
  `devices/{id}/events`, once, so the family sees "missed" without
  asking.

Polling, not a push listener: lost was an FCM (or Firestore listener)
in the Kotlin shell, waking the engine on a new message. That is two
processes to keep in step, a second Firebase SDK in the APK, and an
engine that still has to sync reminders when it starts; a 10-second
poll from the engine is one moving part, and a message a few seconds
late is not a problem for a letter read aloud. Each tick costs about two
document reads (both queries are cursored), so a day of polling stays
well inside Firestore's free quota.

Nothing here decides when she is spoken to: that is the server's lane
(free, no card, `core` takes "initiative", logged by `policy.evaluate`).
After the line is said the server calls `mark_spoken`; her next words
in that conversation go to `record_reply`.

Each call opens its own store connection: the server calls these on the
executor's threads, never the one that opened the runtime's store (the
`tools/reminders.py` pattern). Firestore paths and fields are the shared
contract written down in DECISIONS.md (2026-10-29, Firebase).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any

from saathi.cloud.pairing import FirestoreClient
from saathi.identity.store import IdentityStore, UnknownRow
from saathi.initiative import reminders as reminder_rules
from saathi.tools.reminders import resolve

logger = logging.getLogger(__name__)

# Per tick, at most this many inbox messages are looked at and this
# many reminder events sent; the rest wait for the next tick.
MESSAGE_LIMIT = 20
EVENT_LIMIT = 20
REMINDER_LIMIT = 50

# The `reminder_events` kinds the family's `events` collection takes.
REMOTE_KINDS = ("fired", "acked", "missed")


@dataclass(frozen=True)
class Message:
    id: str
    sender: str
    text: str
    created_at: datetime | None

    @property
    def line(self) -> str:
        """What she hears: their words, verbatim."""
        if self.sender:
            return f"Message from {self.sender}: {self.text}"
        return f"A message: {self.text}"


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return reminder_rules.parse_utc(value)
        except ValueError:
            return None
    return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _latest_links(store: IdentityStore, kind: str) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in store.read("cloud_links", kind=kind):
        current = latest.get(row["remote_id"])
        if current is None or row["id"] > current["id"]:
            latest[row["remote_id"]] = row
    return latest


def _link(store: IdentityStore, kind: str, remote_id: str, local_id, version, now) -> None:
    store.append(
        "cloud_links",
        kind=kind,
        remote_id=remote_id,
        local_id=local_id,
        version=version,
        ts=_iso(now),
    )


class Inbox:
    """One device's inbox, reminders and events in Firestore, against
    the local identity file at `store_path`. `tz` is her wall clock for
    a reminder's "HH:MM" (None: the system's, like the reminder lane)."""

    def __init__(
        self,
        client: FirestoreClient,
        device_id: str,
        store_path: str | Path,
        *,
        tz: tzinfo | None = None,
        device_name: str = "Saathi",
        device_uid: str | None = None,
    ) -> None:
        self.client = client
        self.device_id = device_id
        self.store_path = Path(store_path)
        self.tz = tz
        # For pairing (screen/admin.py): what `devices/{id}` is created with.
        self.device_name = device_name
        self.device_uid = device_uid
        self._lock = threading.Lock()
        self._seen: dict[str, Message] = {}

    @property
    def _device(self) -> str:
        return f"devices/{self.device_id}"

    # -- the tick ----------------------------------------------------------

    def check(self, now: datetime | None = None) -> list[Message]:
        """One poll: reminders in, events out, then the messages still to
        be said, oldest first. A failure syncing reminders or events is
        logged and left for the next tick; one fetching messages raises."""
        now = now or datetime.now(timezone.utc)
        try:
            self.sync_reminders(now)
        except Exception:
            logger.exception("caregiver reminders not synced")
        try:
            self.push_events(now)
        except Exception:
            logger.exception("reminder events not sent")
        return self.unspoken(now)

    def unspoken(self, now: datetime | None = None) -> list[Message]:
        """The messages still to be said, oldest first. The cursor is the
        newest `createdAt` passed: said, or skipped (no text, or already
        `spokenAt` -- an earlier install said it). A skipped one only
        moves the cursor when nothing unsaid is older, so an empty or
        broken message can't hide one before it, and a run of them can't
        hold the query's window shut on the ones after."""
        now = now or datetime.now(timezone.utc)
        with IdentityStore(self.store_path) as store:
            store.create()
            said = _latest_links(store, "message")
        versions = [row["version"] for row in said.values() if row["version"]]
        where: list[tuple[str, str, Any]] = []
        if versions:
            # >=, not >: two messages can share a timestamp, and the one
            # already said is filtered out below by id.
            where.append(("createdAt", ">=", _utc(max(versions))))
        docs = self.client.query(
            self._device, "inbox", where=where, order_by="createdAt", limit=MESSAGE_LIMIT
        )
        messages, passed = [], []
        for doc in docs:
            msg_id = str(doc.get("id") or "")
            if not msg_id or msg_id in said:
                continue
            text = doc.get("text").strip() if isinstance(doc.get("text"), str) else ""
            created = _utc(doc.get("createdAt"))
            if not text or doc.get("spokenAt"):
                if not messages and created is not None:
                    passed.append((msg_id, _iso(created)))
                continue
            sender = doc.get("from").strip() if isinstance(doc.get("from"), str) else ""
            messages.append(Message(msg_id, sender, text, created))
        if passed:
            with IdentityStore(self.store_path) as store:
                for msg_id, version in passed:
                    _link(store, "message", msg_id, None, version, now)
        with self._lock:
            for message in messages:
                self._seen[message.id] = message
        return messages

    # -- after it is said ---------------------------------------------------

    def mark_spoken(self, msg_id: str, now: datetime | None = None) -> None:
        """Recorded here first, so a failed write to the cloud can never
        make her hear it twice; then `spokenAt` on the message and a
        `message_spoken` event for the family."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            message = self._seen.pop(msg_id, None)
        created = _iso(message.created_at) if message is not None else None
        with IdentityStore(self.store_path) as store:
            store.create()
            # No createdAt (never from a query ordered by it): said, but
            # the cursor stays where it was.
            _link(store, "message", msg_id, None, created, now)
        self.client.patch_doc(
            f"{self._device}/inbox/{msg_id}", {"spokenAt": now}, update_mask=["spokenAt"]
        )
        # A fixed id: written again after a retry, it is the same event.
        self.client.patch_doc(
            f"{self._device}/events/message-{msg_id}",
            {
                "ts": now,
                "kind": "message_spoken",
                "text": message.line if message is not None else "",
            },
        )

    def record_reply(self, msg_id: str, text: str, now: datetime | None = None) -> None:
        """Her words after the message, to the one who sent it."""
        text = (text or "").strip()
        if not text:
            return
        now = now or datetime.now(timezone.utc)
        self.client.create_doc(f"{self._device}/inbox/{msg_id}/replies", {"ts": now, "text": text})

    # -- reminders in --------------------------------------------------------

    def sync_reminders(self, now: datetime) -> list[str]:
        """Upserts each caregiver reminder changed since the last sync
        into `reminders`. Returns the remote ids it changed."""
        with IdentityStore(self.store_path) as store:
            store.create()
            links = _latest_links(store, "reminder")
        versions = [row["version"] for row in links.values() if row["version"]]
        where: list[tuple[str, str, Any]] = []
        if versions:
            where.append(("updatedAt", ">=", _utc(max(versions))))
        docs = self.client.query(
            self._device, "reminders", where=where, order_by="updatedAt", limit=REMINDER_LIMIT
        )
        changed = []
        with IdentityStore(self.store_path) as store:
            for doc in docs:
                try:
                    if self._upsert(store, doc, links.get(str(doc.get("id") or "")), now):
                        changed.append(str(doc["id"]))
                except Exception:
                    # One malformed doc must not hold back the rest.
                    logger.exception("caregiver reminder %r not synced", doc.get("id"))
        return changed

    def _upsert(self, store: IdentityStore, doc: dict, link: dict | None, now: datetime) -> bool:
        remote_id = str(doc.get("id") or "")
        if not remote_id:
            return False
        version = _iso(_utc(doc.get("updatedAt"))) or ""
        if link is not None and link["version"] == version:
            return False
        if link is not None and link["local_id"] is not None:
            try:
                store.retire("reminders", int(link["local_id"]), now)
            except UnknownRow:
                pass
        local_id = None
        if doc.get("active", True) is not False:
            local_id = self._local_reminder(store, doc, now)
        _link(store, "reminder", remote_id, local_id, version, now)
        logger.info("caregiver reminder %s -> local %s", remote_id, local_id)
        return True

    def _local_reminder(self, store: IdentityStore, doc: dict, now: datetime) -> int | None:
        text = doc.get("text").strip() if isinstance(doc.get("text"), str) else ""
        repeat = doc.get("repeat") or "none"
        repeat = repeat.strip().lower() if isinstance(repeat, str) else ""
        if not text or repeat not in reminder_rules.RECURRENCES:
            logger.warning("caregiver reminder %s unusable: %r", doc.get("id"), doc)
            return None
        day = doc.get("date") if isinstance(doc.get("date"), str) else None
        first = resolve(
            now,
            time_text=doc.get("time") if isinstance(doc.get("time"), str) else None,
            day=(day or "").strip() or None,
            tz=self.tz,
        )
        if first is None:
            logger.warning("caregiver reminder %s has no usable time: %r", doc.get("id"), doc)
            return None
        return store.append(
            "reminders",
            due_at=first.isoformat(),
            text=text,
            recurrence=None if repeat == "none" else repeat,
            active=1,
        )

    # -- events out ----------------------------------------------------------

    def push_events(self, now: datetime) -> int:
        """Sends `reminder_events` rows not sent yet, in order, and stops
        at the first failure so none is skipped. Returns how many went."""
        with IdentityStore(self.store_path) as store:
            store.create()
            cursor = max(
                (int(row["local_id"]) for row in store.read("cloud_links", kind="event")),
                default=0,
            )
            events = sorted(
                (e for e in store.read("reminder_events") if e["id"] > cursor),
                key=lambda e: e["id"],
            )[:EVENT_LIMIT]
            if not events:
                return 0
            texts = {row["id"]: row["text"] for row in store.read("reminders")}
            # Every local row a caregiver reminder ever had, so an event
            # of one since edited still names it.
            remote_of = {
                int(row["local_id"]): row["remote_id"]
                for row in store.read("cloud_links", kind="reminder")
                if row["local_id"] is not None
            }
        count = 0
        with IdentityStore(self.store_path) as store:
            for event in events:
                remote_id = f"reminder-{event['id']}-{event['ts'][:19].replace(':', '')}"
                if event["kind"] in REMOTE_KINDS:
                    fields: dict[str, Any] = {
                        "ts": _utc(event["ts"]) or now,
                        "kind": event["kind"],
                        "text": texts.get(event["reminder_id"], ""),
                    }
                    if event["reminder_id"] in remote_of:
                        fields["reminderId"] = remote_of[event["reminder_id"]]
                    # patch, not create, at a fixed id: sent again after a
                    # crash between here and the link below, it is the
                    # same document, not a second "missed".
                    self.client.patch_doc(f"{self._device}/events/{remote_id}", fields)
                _link(store, "event", remote_id, event["id"], None, now)
                count += 1
        return count
