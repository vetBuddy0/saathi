"""The developer console: `/admin`, for whoever builds or sets up Saathi --
never her screen.

Why it exists: what Saathi "knows" and how it decides what to say lived
in a SQLite file and five modules, readable only by someone who had read
the code. The owner asked (2026-10-28) for one place that shows exactly
how memory and personality work on the AI layer -- what is stored, what
is sent, why -- and lets a developer correct it, plus her contacts and
the keys. One web page served by the engine, so the laptop's browser and
the tablet (the shell opens it from its setup dialog) see the same thing.

Everything here goes through the same doors the voice side uses, so the
console can't make a state the rest of the system doesn't understand:
- **Memory** is `IdentityStore`'s create/append/read/retire and nothing
  more (one of the five interfaces; unchanged). Editing a rule retires the
  old one and appends the new, with an episode recording where it came
  from -- SPEC.md: every rule records its source. Episodes are append-only
  by design; they can be added, not edited or removed.
- **Contacts** are `call/contacts.py`'s convention (latest row per name
  wins). Removing one appends a row with no number, so it stops being
  callable and the history stays.
- **Personality** is the persona file `identity/compile.py` reads; an edit
  goes to `<data_dir>/persona.txt`, which wins over the shipped
  `persona_stub.txt` (so an app update can't undo it), and "reset"
  deletes that override.
- **Keys** are shown as set/missing only, never a value. On the phone the
  shell owns them (encrypted prefs); the page writes them through the
  shell's bridge, not through here.
- **Pairing a phone** (2026-10-29) writes a 6-digit code to the family
  cloud (`cloud/pairing.py`) through the same client the inbox polls
  with; with no cloud on this run it answers 409 "Cloud is not set up"
  rather than a code nobody can claim.

After an edit the session recompiles her context in the background
(`refresh_context`), between turns like every compile, so the next turn
uses it.

Loopback only: the console edits her memory and her contacts. On the
phone the engine listens on 127.0.0.1 anyway; on a laptop bound wider,
another machine on the network gets 403. Lost: a password (one more
secret to keep for a page that only the device itself should open).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

from saathi.call import contacts
from saathi.identity import compile as compile_mod
from saathi.identity.preferences import (
    CAPTIONS_KEY,
    LANGUAGE_KEY,
    TTS_BACKEND_KEY,
    REMINDER_REPEAT_KEY,
    REMINDER_TRIES_KEY,
    WAKE_WORD_KEY,
    write_preference,
)
from saathi.identity.store import IdentityStore, UnknownRow

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static" / "admin"
LOOPBACK = ("127.0.0.1", "::1")
MAX_EPISODES = 300
MAX_TURNS = 20
MAX_TEXT = 2000
SOURCE_PREFIX = "Noted by the care team: "
CLOUD_NOT_SET_UP = "Cloud is not set up"

# What each key the engine reads is for, in the order the console lists
# them. `GOOGLE_APPLICATION_CREDENTIALS` is a path to a JSON file; the
# phone's keys page takes the JSON itself (`..._JSON`, android.py).
KEYS: list[tuple[str, str]] = [
    ("OPENAI_API_KEY", "Hears her (speech to text) and writes the replies, when set."),
    ("GROQ_API_KEY", "The other AI provider: used when OpenAI isn't set, and as fallback."),
    ("YOUTUBE_API_KEY", "Finds songs and videos to play."),
    ("GOOGLE_APPLICATION_CREDENTIALS", "Google Cloud voice (Chirp 3 HD), the warm voice."),
    ("TWILIO_ACCOUNT_SID", "Twilio account (calls)."),
    ("TWILIO_API_KEY", "Twilio API key SID (SK...), signs call tokens."),
    ("TWILIO_API_SECRET", "Twilio API key secret."),
    ("TWILIO_FROM_NUMBER", "The Twilio number calls come from."),
    ("TWILIO_TEST_NUMBER", "What “call the test number” rings."),
    ("TWILIO_TWIML_APP_SID", "TwiML App (AP...) the tablet's calls dial through."),
    ("FIREBASE_API_KEY", "Firebase web API key: copies conversations, clips and memory to Firebase."),
    ("FIREBASE_PROJECT_ID", "The Firebase project id, e.g. saathi-family."),
    ("FIREBASE_STORAGE_BUCKET", "The project's Storage bucket, e.g. saathi-family.firebasestorage.app."),
]
PREFERENCE_KEYS = (
    LANGUAGE_KEY,
    TTS_BACKEND_KEY,
    CAPTIONS_KEY,
    WAKE_WORD_KEY,
    REMINDER_REPEAT_KEY,
    REMINDER_TRIES_KEY,
)

# What each table in identity.sqlite3 is for, who writes it, and whether
# any of it reaches the model -- the "Under the hood" tab. The columns
# are not repeated here: they are read from the store's own schema, so
# the page cannot drift from the database.
TABLES: dict[str, tuple[str, str, str]] = {
    "episodes": (
        "Things that happened, one sentence each, with an importance score (1-10) and a "
        "vector for relevance. Append-only: the record everything else cites.",
        "Written after every exchange, between turns (identity/digest.py); also this console.",
        "The 5 best-scoring are sent each turn, as sentences.",
    ),
    "rules": (
        "What Saathi has learned about her, one sentence each, with a confidence and the "
        "episode it came from. active=0 means retired (corrected), kept for history.",
        "This console, and her own corrections (the correct_memory tool retires one). "
        "Automatic learning (identity/reflect.py) exists but is not run by the app yet.",
        "Every active rule is sent every turn, as [memory N] sentences; the confidence never is.",
    ),
    "entities": (
        "People (and other things) she mentions: her contacts live here, with the phone "
        "number in notes as JSON.",
        "Saving a contact (her voice or this console).",
        "Not sent; looked up when she asks to call someone.",
    ),
    "edges": (
        "How people relate to her: \"son\", \"daughter\"... with since/until, so a "
        "relation can end without being deleted.",
        "Saving a contact with a relation.",
        "Not sent; it is how \"call my son\" finds a number.",
    ),
    "preferences": (
        "Settings as key/value rows; the latest row for a key wins (language, voice, captions).",
        "Her voice (\"speak Mandarin\"), the Ctrl+L panel, this console.",
        "Language becomes one instruction (\"Reply in english.\"); the rest steer the device.",
    ),
    "reminders": (
        "Things due at a time, optionally repeating.",
        "set_reminder when she asks; cancel_reminder retires one.",
        "Not sent; said in her own words when due.",
    ),
    "reminder_events": (
        "What happened each time a reminder came due: said, done, or missed.",
        "The reminder tick and her \"done\" (card or voice).",
        "Not sent.",
    ),
    "cloud_links": (
        "What the family's app already has from this device: which of their reminders "
        "became which local one (and at which edit), which messages were said, which "
        "reminder events were sent. The latest row per item wins.",
        "The family inbox poll (cloud/inbox.py), every few seconds.",
        "Not sent.",
    ),
    "turns": (
        "One row per reply: how long each stage took, tokens, cost, which voice spoke. "
        "No words: what she said is not kept here.",
        "Every turn, after the reply.",
        "Never sent; it is the latency and cost record.",
    ),
    "initiatives": (
        "Times Saathi considered speaking first, and why it did or didn't.",
        "Not written by the app yet.",
        "Not sent.",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise web.HTTPBadRequest(text=f"{field} is required")
    value = value.strip()
    if len(value) > MAX_TEXT:
        raise web.HTTPBadRequest(text=f"{field} is too long (max {MAX_TEXT} characters)")
    return value


def _key_status(name: str) -> bool:
    value = os.environ.get(name, "").strip()
    if name == "GOOGLE_APPLICATION_CREDENTIALS":
        return bool(value) and Path(value).is_file()
    return bool(value)


class Console:
    """The console's reads and writes, on `store_path` with a connection
    of its own per call (the `call/contacts.py` pattern: handlers run on
    the executor, never the thread that opened the runtime's store)."""

    def __init__(
        self,
        store_path: Path,
        *,
        session: Any = None,
        info: Callable[[], dict[str, Any]] | None = None,
        settings_changed: Callable[[], None] | None = None,
        cloud: Any = None,
    ) -> None:
        self.store_path = Path(store_path)
        self.session = session
        self._info = info
        # The family cloud (cloud/inbox.py's Inbox: `.client`, `.device_id`),
        # or None when this device has none -- pairing then answers 409.
        self.cloud = cloud
        # Called on the loop after a setting is written, so an open face
        # shows it now (captions on the tablet, 2026-10-29) rather than
        # after its next reconnect.
        self.settings_changed = settings_changed or (lambda: None)

    # -- reads ------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        with IdentityStore(self.store_path) as store:
            store.create()
            counts = {
                "rules": sum(1 for r in store.read("rules") if r.get("active", 1)),
                "rules_retired": sum(1 for r in store.read("rules") if not r.get("active", 1)),
                "episodes": len(store.read("episodes")),
                "people": len(contacts.list_contacts(self.store_path)),
                "turns": len(store.read("turns")),
            }
        describe = getattr(self.session, "describe", None)
        engine = describe() if callable(describe) else None
        return {
            "counts": counts,
            "engine": engine,
            "runtime": self._info() if self._info is not None else {},
            "persona_source": (
                "edited" if compile_mod.persona_override_path().is_file() else "shipped"
            ),
        }

    def memory(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with IdentityStore(self.store_path) as store:
            store.create()
            episodes = store.read("episodes")
            rules = store.read("rules")
            preferences = store.read("preferences")
            turns = store.read("turns")
            reminders = store.read("reminders")
        by_episode = {e["id"]: e for e in episodes}
        query = compile_mod._newest_episode_embedding(episodes)
        sent = compile_mod.retrieve_episodes(episodes, now=now, query_embedding=query)
        sent_ids = [e["id"] for e in sent]
        latest_pref: dict[str, dict] = {}
        for row in preferences:
            current = latest_pref.get(row["key"])
            if current is None or row["updated_at"] >= current["updated_at"]:
                latest_pref[row["key"]] = row
        snapshot = getattr(self.session, "conversation_snapshot", None)
        return {
            "rules": [
                {
                    "id": r["id"],
                    "text": r["text"],
                    "confidence": r.get("confidence"),
                    "learned_at": r["learned_at"],
                    "active": bool(r.get("active", 1)),
                    "source": (by_episode.get(r.get("source_episode")) or {}).get("text"),
                }
                for r in sorted(rules, key=lambda r: r["id"], reverse=True)
            ],
            "episodes": [
                {
                    "id": e["id"],
                    "ts": e["ts"],
                    "text": e["text"],
                    "importance": e.get("importance"),
                    "embedded": e.get("embedding") is not None,
                    "sent": e["id"] in sent_ids,
                }
                for e in sorted(episodes, key=lambda e: e["id"], reverse=True)[:MAX_EPISODES]
            ],
            "sent_episode_ids": sent_ids,
            "preferences": {k: v["value"] for k, v in sorted(latest_pref.items())},
            "reminders": [
                {k: r[k] for k in ("id", "due_at", "text", "recurrence", "active")}
                for r in reminders
            ],
            "turns": [
                {k: t.get(k) for k in (
                    "id", "ts", "mode", "stt_ms", "first_token_ms", "first_tts_chunk_ms",
                    "prompt_tokens", "completion_tokens", "cost_usd", "engine", "voice",
                )}
                for t in sorted(turns, key=lambda t: t["id"], reverse=True)[:MAX_TURNS]
            ],
            "short_term": snapshot() if callable(snapshot) else None,
        }

    def layout(self) -> dict[str, Any]:
        """Where memory lives and how a turn's context is put together,
        with the numbers the code actually uses (the "Under the hood"
        tab). Read from the modules themselves, never restated."""
        import importlib.util
        import re

        from saathi.identity import embed
        from saathi.identity.store import _SCHEMA
        from saathi.screen.emotion import EMOTIONS
        from saathi.voice import conversation

        with IdentityStore(self.store_path) as store:
            store.create()
            counts = {name: len(store.read(name)) for name in _SCHEMA}
        tables = []
        for name, ddl in _SCHEMA.items():
            purpose, written_by, sent = TABLES.get(name, ("", "", ""))
            columns = [c for c in re.findall(r"^\s+(\w+) [A-Z]", ddl, re.M) if c != "CREATE"]
            tables.append({
                "name": name, "columns": columns, "rows": counts[name],
                "purpose": purpose, "written_by": written_by, "sent": sent,
            })
        override = compile_mod.persona_override_path()
        relevance = embed.model_files_present() and (
            importlib.util.find_spec("onnxruntime") is not None
        )
        return {
            "files": {
                "data_dir": str(override.parent),
                "database": str(self.store_path),
                "database_bytes": (
                    self.store_path.stat().st_size if self.store_path.is_file() else 0
                ),
                "persona_edited": str(override) if override.is_file() else None,
                "persona_shipped": str(compile_mod._BASE_PERSONA_PATH),
            },
            "tables": tables,
            "retrieval": {
                "top_k": compile_mod._TOP_K_EPISODES,
                "recency_decay_per_hour": compile_mod._RECENCY_DECAY_PER_HOUR,
                "relevance_on": relevance,
            },
            "window": {
                "exchanges": conversation.MAX_EXCHANGES,
                "tokens": conversation.MAX_TOKENS,
            },
            "code_sentences": {
                "memory_numbers": compile_mod._MEMORY_NUMBERS_SENTENCE,
                "length_cap": compile_mod._LENGTH_CAP_SENTENCE,
            },
            "moods": sorted(EMOTIONS),
        }

    def prompt(self) -> dict[str, Any]:
        path = compile_mod.persona_path()
        with IdentityStore(self.store_path) as store:
            store.create()
            compiled = compile_mod.compile_context(store)
        return {
            "persona": path.read_text(),
            "persona_source": (
                "edited" if path == compile_mod.persona_override_path() else "shipped"
            ),
            "compiled": compiled,
        }

    def contact_list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone,
                "country": c.country,
                "relations": list(c.relations),
            }
            for c in contacts.list_contacts(self.store_path)
        ]

    def keys(self) -> list[dict[str, Any]]:
        return [{"name": n, "purpose": p, "set": _key_status(n)} for n, p in KEYS]

    # -- writes -----------------------------------------------------------

    def _provenance(self, store: IdentityStore, text: str) -> int:
        """The episode a console-written rule traces to. Worded as a note,
        because it can be retrieved into her context like any episode."""
        return store.append(
            "episodes", ts=_now(), entity_id=None, text=SOURCE_PREFIX + text,
            importance=0.5, embedding=None,
        )

    def add_rule(self, text: str) -> int:
        with IdentityStore(self.store_path) as store:
            store.create()
            source = self._provenance(store, text)
            rule = store.append(
                "rules", text=text, confidence=1.0, learned_at=_now(),
                source_episode=source, active=1,
            )
        self._refresh()
        return rule

    def edit_rule(self, rule_id: int, text: str) -> int:
        with IdentityStore(self.store_path) as store:
            store.create()
            if not any(r["id"] == rule_id for r in store.read("rules")):
                raise web.HTTPNotFound(text=f"no memory {rule_id}")
            store.retire("rules", rule_id, _now())
            source = self._provenance(store, text)
            rule = store.append(
                "rules", text=text, confidence=1.0, learned_at=_now(),
                source_episode=source, active=1,
            )
        self._refresh()
        return rule

    def retire_rule(self, rule_id: int) -> None:
        with IdentityStore(self.store_path) as store:
            store.create()
            try:
                store.retire("rules", rule_id, _now())
            except UnknownRow:
                raise web.HTTPNotFound(text=f"no memory {rule_id}") from None
        self._refresh()

    def add_episode(self, text: str, importance: float) -> int:
        with IdentityStore(self.store_path) as store:
            store.create()
            episode = store.append(
                "episodes", ts=_now(), entity_id=None, text=text,
                importance=importance, embedding=None,
            )
        self._refresh()
        return episode

    def save_persona(self, text: str) -> None:
        path = compile_mod.persona_override_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + "\n")
        self._refresh()

    def reset_persona(self) -> None:
        compile_mod.persona_override_path().unlink(missing_ok=True)
        self._refresh()

    def save_contact(self, name: str, phone: str, relation: str | None, country: str | None) -> int:
        from saathi.call.numbers import parse_spoken_number
        from saathi.call.phone import resolve

        resolved = resolve(parse_spoken_number(phone), (country or "").upper() or None)
        if not resolved.complete:
            raise web.HTTPBadRequest(
                text="That number isn't complete: start it with + and the country code "
                "(e.g. +65 8961 4304), or pick the country."
            )
        return contacts.save_contact(
            self.store_path, name, resolved.e164, resolved.country, relation or None
        )

    def remove_contact(self, name: str) -> None:
        if contacts.find_by_exact_name(self.store_path, name) is None:
            raise web.HTTPNotFound(text=f"no contact called {name}")
        with IdentityStore(self.store_path) as store:
            store.create()
            store.append(
                "entities", kind=contacts.PERSON_KIND, name=name.strip(),
                notes=json.dumps({"phone": None, "removed": True}), created_at=_now(),
            )

    # -- reminders (owner, 2026-10-29: "adjustable in settings") ---------
    # The same rows and rules her voice uses (tools/reminders.py,
    # initiative/reminders.py). Editing retires the old reminder and adds
    # the new one -- the store has no update -- so its history stays.

    def reminder_list(self) -> dict[str, Any]:
        from saathi.initiative import reminders as rules
        from saathi.tools.reminders import describe

        now = datetime.now(timezone.utc)
        with IdentityStore(self.store_path) as store:
            store.create()
            rows = store.read("reminders", active=1)
            texts = {row["id"]: row["text"] for row in store.read("reminders")}
            events = store.read("reminder_events")
            repeat_after, tries = rules.settings(store)
        listed = []
        for row in rows:
            first = rules.local(rules.parse_utc(row["due_at"]))
            upcoming = rules.next_occurrence(row, now)
            listed.append(
                {
                    "id": row["id"],
                    "text": row["text"],
                    "repeat": row["recurrence"] or "none",
                    "time": first.strftime("%H:%M"),
                    "date": first.date().isoformat(),
                    "next": describe(upcoming, now) if upcoming else "due now",
                }
            )
        history = [
            {"ts": e["ts"], "kind": e["kind"], "text": texts.get(e["reminder_id"], "")}
            for e in sorted(events, key=lambda e: e["ts"], reverse=True)[:20]
        ]
        return {
            "reminders": listed,
            "history": history,
            "repeat_minutes": int(repeat_after.total_seconds() // 60),
            "tries": tries,
            "repeat_choices": list(rules.REPEAT_CHOICES),
            "tries_choices": list(rules.TRIES_CHOICES),
        }

    def save_reminder(
        self, text: str, time_text: str, day: str | None, repeat: str, replaces: int | None
    ) -> int:
        from saathi.initiative import reminders as rules
        from saathi.tools.reminders import resolve

        if repeat not in rules.RECURRENCES:
            raise web.HTTPBadRequest(text=f"repeat is one of {', '.join(rules.RECURRENCES)}")
        now = datetime.now(timezone.utc)
        first = resolve(now, time_text=time_text, day=day or None)
        if first is None:
            raise web.HTTPBadRequest(
                text="Pick a time (HH:MM) and, if you give a date, one not in the past."
            )
        with IdentityStore(self.store_path) as store:
            store.create()
            if replaces is not None:
                if not store.read("reminders", id=replaces, active=1):
                    raise web.HTTPNotFound(text=f"no active reminder {replaces}")
                store.retire("reminders", replaces, now)
            return store.append(
                "reminders",
                due_at=first.isoformat(),
                text=text,
                recurrence=None if repeat == "none" else repeat,
                active=1,
            )

    def cancel_reminder(self, reminder_id: int) -> None:
        with IdentityStore(self.store_path) as store:
            store.create()
            if not store.read("reminders", id=reminder_id, active=1):
                raise web.HTTPNotFound(text=f"no active reminder {reminder_id}")
            store.retire("reminders", reminder_id, datetime.now(timezone.utc))

    def set_preference(self, key: str, value: str) -> None:
        from saathi.initiative import reminders as rules

        if key not in PREFERENCE_KEYS:
            raise web.HTTPBadRequest(text=f"{key} is not a setting the console changes")
        choices = {
            REMINDER_REPEAT_KEY: rules.REPEAT_CHOICES,
            REMINDER_TRIES_KEY: rules.TRIES_CHOICES,
        }.get(key)
        if choices is not None and value not in {str(c) for c in choices}:
            raise web.HTTPBadRequest(text=f"{key} is one of {', '.join(map(str, choices))}")
        with IdentityStore(self.store_path) as store:
            store.create()
            write_preference(store, key, value)

    # -- pairing a caregiver's phone (cloud/pairing.py) -------------------
    # The code is written to Firestore by the device; the caregiver's app
    # claims it. The console only shows it, its QR, and who has paired.

    def _cloud(self) -> Any:
        if self.cloud is None or getattr(self.cloud, "client", None) is None:
            raise web.HTTPConflict(text=CLOUD_NOT_SET_UP)
        return self.cloud

    def paired(self) -> dict[str, Any]:
        from saathi.cloud import pairing

        cloud = self._cloud()
        try:
            caregivers = pairing.paired_caregivers(cloud.client, cloud.device_id)
        except Exception as exc:
            logger.exception("paired caregivers not read")
            raise web.HTTPBadGateway(text=f"Couldn't reach the cloud: {exc}") from None
        return {"caregivers": caregivers}

    def pair(self) -> dict[str, Any]:
        from saathi.call.family_server import qr_svg
        from saathi.cloud import pairing

        cloud = self._cloud()
        now = datetime.now(timezone.utc)
        try:
            device = pairing.ensure_device(
                cloud.client,
                cloud.device_id,
                now,
                name=getattr(cloud, "device_name", None) or "Saathi",
                device_uid=getattr(cloud, "device_uid", None),
            )
            code, expires_at = pairing.issue_code(cloud.client, cloud.device_id, now)
        except Exception as exc:
            logger.exception("pairing code not issued")
            raise web.HTTPBadGateway(text=f"Couldn't reach the cloud: {exc}") from None
        return {
            "code": code,
            "expires_at": expires_at.isoformat(),
            "seconds": int((expires_at - now).total_seconds()),
            "qr_svg": qr_svg(pairing.QR_PREFIX + code),
            "caregivers": pairing.caregivers_of(device),
        }

    def _refresh(self) -> None:
        refresh = getattr(self.session, "refresh_context", None)
        if callable(refresh):
            refresh()


def add_routes(app: web.Application, console: Console) -> None:
    """`/admin` and `/admin/api/*` on the screen server's app."""

    def loopback(handler):
        async def guarded(request: web.Request) -> web.StreamResponse:
            if request.remote not in LOOPBACK:
                raise web.HTTPForbidden(text="The developer console opens on this device only.")
            return await handler(request)

        return guarded

    async def run(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def body(request: web.Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="expected a JSON body") from None
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="expected a JSON object")
        return data

    def rule_id(request: web.Request) -> int:
        try:
            return int(request.match_info["rule_id"])
        except ValueError:
            raise web.HTTPNotFound() from None

    @loopback
    async def page(_request):
        return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    def getter(fn):
        @loopback
        async def handler(_request):
            return web.json_response(await run(fn), headers={"Cache-Control": "no-store"})

        return handler

    @loopback
    async def add_rule(request):
        data = await body(request)
        new_id = await run(console.add_rule, _text(data.get("text"), "text"))
        return web.json_response({"id": new_id})

    @loopback
    async def edit_rule(request):
        data = await body(request)
        new_id = await run(console.edit_rule, rule_id(request), _text(data.get("text"), "text"))
        return web.json_response({"id": new_id})

    @loopback
    async def retire_rule(request):
        await run(console.retire_rule, rule_id(request))
        return web.json_response({"ok": True})

    @loopback
    async def add_episode(request):
        data = await body(request)
        importance = data.get("importance", 0.5)
        if not isinstance(importance, (int, float)) or not 0 <= importance <= 1:
            raise web.HTTPBadRequest(text="importance is a number from 0 to 1")
        new_id = await run(console.add_episode, _text(data.get("text"), "text"), float(importance))
        return web.json_response({"id": new_id})

    @loopback
    async def save_persona(request):
        data = await body(request)
        await run(console.save_persona, _text(data.get("text"), "text"))
        return web.json_response({"ok": True})

    @loopback
    async def reset_persona(_request):
        await run(console.reset_persona)
        return web.json_response({"ok": True})

    @loopback
    async def save_contact(request):
        data = await body(request)
        relation = data.get("relation") if isinstance(data.get("relation"), str) else None
        country = data.get("country") if isinstance(data.get("country"), str) else None
        new_id = await run(
            console.save_contact,
            _text(data.get("name"), "name"),
            _text(data.get("phone"), "phone"),
            (relation or "").strip() or None,
            country,
        )
        return web.json_response({"id": new_id})

    @loopback
    async def remove_contact(request):
        data = await body(request)
        await run(console.remove_contact, _text(data.get("name"), "name"))
        return web.json_response({"ok": True})

    @loopback
    async def set_preference(request):
        data = await body(request)
        await run(
            console.set_preference, _text(data.get("key"), "key"), _text(data.get("value"), "value")
        )
        console.settings_changed()
        return web.json_response({"ok": True})

    def reminder_id(request: web.Request) -> int:
        try:
            return int(request.match_info["reminder_id"])
        except ValueError:
            raise web.HTTPNotFound() from None

    @loopback
    async def save_reminder(request):
        data = await body(request)
        replaces = data.get("replaces")
        if replaces is not None and not isinstance(replaces, int):
            raise web.HTTPBadRequest(text="replaces is a reminder id")
        day = data.get("date") if isinstance(data.get("date"), str) else None
        repeat = data.get("repeat") if isinstance(data.get("repeat"), str) else "none"
        new_id = await run(
            console.save_reminder,
            _text(data.get("text"), "text"),
            _text(data.get("time"), "time"),
            (day or "").strip() or None,
            repeat,
            replaces,
        )
        return web.json_response({"id": new_id})

    @loopback
    async def cancel_reminder(request):
        await run(console.cancel_reminder, reminder_id(request))
        return web.json_response({"ok": True})

    app.router.add_get("/admin", page)
    app.router.add_get("/admin/api/overview", getter(console.overview))
    app.router.add_get("/admin/api/memory", getter(console.memory))
    app.router.add_get("/admin/api/prompt", getter(console.prompt))
    app.router.add_get("/admin/api/contacts", getter(console.contact_list))
    app.router.add_get("/admin/api/keys", getter(console.keys))
    app.router.add_get("/admin/api/layout", getter(console.layout))
    app.router.add_post("/admin/api/rules", add_rule)
    app.router.add_post("/admin/api/rules/{rule_id}", edit_rule)
    app.router.add_post("/admin/api/rules/{rule_id}/retire", retire_rule)
    app.router.add_post("/admin/api/episodes", add_episode)
    app.router.add_put("/admin/api/persona", save_persona)
    app.router.add_delete("/admin/api/persona", reset_persona)
    app.router.add_post("/admin/api/contacts", save_contact)
    app.router.add_post("/admin/api/contacts/remove", remove_contact)
    app.router.add_post("/admin/api/preferences", set_preference)
    app.router.add_get("/admin/api/reminders", getter(console.reminder_list))
    app.router.add_post("/admin/api/reminders", save_reminder)
    app.router.add_post("/admin/api/reminders/{reminder_id}/cancel", cancel_reminder)
    @loopback
    async def pair(_request):
        return web.json_response(await run(console.pair), headers={"Cache-Control": "no-store"})

    app.router.add_get("/admin/api/pairing", getter(console.paired))
    app.router.add_post("/admin/api/pairing", pair)
    app.router.add_static("/admin/static/", STATIC_DIR)
