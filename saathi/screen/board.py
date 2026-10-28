"""What the device's own screen shows when she taps a tab: her messages,
her reminders on a calendar, her family to call, and the pairing code.

Why it exists: the owner (2026-10-28) found that everything she could
ask Melody for by voice was invisible on the screen. A family message
appeared only as a caption, reminders and calls had no page at all, and
pairing a phone needed Ctrl+P. They asked for "clickable things on the
screen", with tabs. This module reads that state. `static/js/board.js`
draws it.

The line it keeps (SPEC.md, "The face"): on-screen text is content (a
name, a reminder, a message), never status. Nothing here says what the
device is doing.

Reads only. Anything a tap *does* (ring someone) goes through the tool
registry's permission check like her voice does (`SCREEN_TOOLS`,
screen/server.py), never through here. Lost: a second write path for
the screen. "The tool executes" (CLAUDE.md) means one door for actions,
and that door is the registry.

Lost: pushing the whole board over the face's WebSocket on every change.
The tabs are opened rarely and read once, so a GET when one opens is
simpler and can't go stale. Live events (a message being read to her)
still arrive on the socket as `shown` cards.

Loopback only, like the pairing routes (call/family_server.py): the
screen server listens on 127.0.0.1, but a tunnel or a changed
SAATHI_SCREEN_HOST must never turn her messages into a public page.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

from saathi.identity.store import IdentityStore

# The calendar shows this many days from today: this month and the next
# few weeks, which is as far ahead as anyone sets a tablet reminder.
CALENDAR_DAYS = 42
# Which tools a tap on the screen may ask for. A tap reaches the same
# permission check as her voice. This list only narrows what the
# screen can ask, and it is enforced in screen/server.py.
SCREEN_TOOLS = frozenset({"call_contact"})


def _aware(ts: str) -> datetime:
    when = datetime.fromisoformat(ts)
    return when if when.tzinfo is not None else when.astimezone()


def _clock(when: datetime) -> str:
    hour = when.hour % 12 or 12
    minutes = "" if when.minute == 0 else f":{when.minute:02d}"
    return f"{hour}{minutes} {'AM' if when.hour < 12 else 'PM'}"


def reminder_days(
    identity_db: Path, now: datetime | None = None, days: int = CALENDAR_DAYS
) -> dict[str, Any]:
    """Her active reminders over the next `days`, by date. A daily or
    weekly one is shown on every day it will fire, because that is what she
    will hear, though storage holds only its next due time
    (initiative/reminders.py rolls it forward)."""
    now = now or datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    by_day: dict[str, list[dict[str, Any]]] = {}
    with IdentityStore(Path(identity_db)) as store:
        rows = store.read("reminders", active=1)
    for row in rows:
        due = _aware(row["due_at"]).astimezone(now.tzinfo)
        recurrence = row["recurrence"]
        step = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}.get(recurrence)
        while due < end:
            if due >= start:
                by_day.setdefault(due.date().isoformat(), []).append(
                    {
                        "id": row["id"],
                        "text": row["text"],
                        "at": _clock(due),
                        "repeat": recurrence or "once",
                        "_t": due.timestamp(),
                    }
                )
            if step is None:
                break
            due += step
    for items in by_day.values():
        items.sort(key=lambda item: item["_t"])
        for item in items:
            del item["_t"]
    return {"today": start.date().isoformat(), "days": days, "by_day": by_day}


def family_list(registry: Any) -> list[dict[str, Any]]:
    """Who she can ring from the screen: everyone paired."""
    return [
        {"name": m.name, "relation": m.relation or "", "alerts": m.subscription is not None}
        for m in registry.members()
    ]


def _is_loopback(request: web.Request) -> bool:
    peer = request.remote or ""
    return peer in ("127.0.0.1", "::1") or peer.startswith("127.")


def install_board_routes(
    app: web.Application,
    *,
    identity_db: Path | None,
    hub: Callable[[], Any] = lambda: None,
    registry: Callable[[], Any] = lambda: None,
) -> None:
    """GET /screen-local/board/{messages,reminders,family}. Each answers
    `{"ok": false, "reason": ...}` when its source is off, in words for
    her, so an empty tab is never a blank screen."""

    async def _answer(request: web.Request, build: Callable[[], Any]) -> web.Response:
        if not _is_loopback(request):
            raise web.HTTPForbidden()
        result = await asyncio.get_running_loop().run_in_executor(None, build)
        return web.json_response(result)

    def _messages() -> dict[str, Any]:
        source = hub()
        if source is None:
            return {"ok": False, "reason": "Messages come through the family app, which is off."}
        return {"ok": True, "items": source.recent()}

    def _reminders() -> dict[str, Any]:
        if identity_db is None:
            return {"ok": False, "reason": "No reminders here."}
        return {"ok": True, **reminder_days(identity_db)}

    def _family() -> dict[str, Any]:
        source = registry()
        if source is None:
            return {"ok": False, "reason": "Calling the family needs the family app, which is off."}
        return {"ok": True, "members": family_list(source)}

    builders = {"messages": _messages, "reminders": _reminders, "family": _family}

    async def board(request: web.Request) -> web.Response:
        build = builders.get(request.match_info["what"])
        if build is None:
            raise web.HTTPNotFound()
        return await _answer(request, build)

    app.router.add_get("/screen-local/board/{what}", board)

