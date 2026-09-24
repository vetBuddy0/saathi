"""`IdentityStore` — one of the five interfaces. SQLite, the schema from
SPEC.md, and four verbs: `create`, `append`, `read`, `retire`.

This is deliberately not a repository with one method per table. SPEC.md's
"Memory" section draws the real boundary: *stored* is rows, confidence and
provenance; *sent* is plain sentences, and that translation is
`identity/compile.py`'s job, not this file's. Retrieval scoring
(`reflect.py`), the compiled context block (`compile.py`), and the
family-editable view (`profile.py`) all build on top of `append`/`read` —
none of that domain logic belongs here, or checkpoint 1's "nothing but
create, append and read" stops meaning anything.

**Why `retire` exists (2026-09-25), and what it deliberately is not.**
Checkpoints 1 through 3 ran on `create`/`append`/`read` alone, and three
real features hit the same wall: retracting a learned rule
(`rules.active`), silencing a reminder (`reminders.active`), and ending
a relationship (`edges.until`). `preferences` got around it by becoming
an append-only log where the latest row wins, but that doesn't transfer:
a family member retracting rule #47 needs *that row* to stop being
believed, not "the latest rule about this topic." Two shapes were on the
table (`docs/completed/checkpoint-3.md`): (a) one narrow primitive on
the interface, or (b) append-only event tables per flag
(`rule_retractions`, `reminder_completions`, ...) with every reader
computing effective state. (b) lost: it keeps the interface untouched at
the cost of three more tables and read-side logic in every consumer
(`compile.py`, `profile.py`, `initiative/policy.py`), and it makes the
one question that matters — "does the device still believe this?" —
answerable only by a join. The user chose (a). `retire(table, row_id,
at)` is that primitive and nothing more: it marks one row as having
stopped being true. It is not `update()`; it takes no column name and
no value, and it refuses tables that have no notion of "no longer
true". An append-only store that cannot be corrected is worse than one
that forgets — a device holding a wrong belief about an elderly person
with no way to fix it is the worst failure this product has.

One SQLite file on the device, not a service — "the identity file is the
product's asset and lives where the device is" (SPEC.md). `embedding` is
a BLOB of little-endian float32 (`identity/embed.py` writes it,
`identity/compile.py` reads it back with `np.frombuffer`); `sqlite-vec`
or brute-force numpy cosine both read it the same way, so nothing here
needs to know which.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

# Column order matters only for readability here — `append`/`read` always
# bind by name, never by position.
_SCHEMA: dict[str, str] = {
    "entities": """
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """,
    "edges": """
        CREATE TABLE IF NOT EXISTS edges (
            src INTEGER NOT NULL REFERENCES entities(id),
            dst INTEGER NOT NULL REFERENCES entities(id),
            relation TEXT NOT NULL,
            since TEXT,
            until TEXT
        )
    """,
    "episodes": """
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            entity_id INTEGER REFERENCES entities(id),
            text TEXT NOT NULL,
            importance REAL,
            embedding BLOB
        )
    """,
    "rules": """
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            confidence REAL,
            learned_at TEXT NOT NULL,
            source_episode INTEGER REFERENCES episodes(id),
            active INTEGER NOT NULL DEFAULT 1
        )
    """,
    # id + no UNIQUE/PRIMARY KEY on `key`: an append-only log, not a
    # key-value table -- append() (IdentityStore's only write primitive)
    # can't express "update the row for this key", and a PRIMARY KEY on
    # `key` made the *first* write to a key succeed and every write
    # after that raise sqlite3.IntegrityError (found live: it broke the
    # Ctrl+L panel and the spoken "speak to me in Mandarin" tool the
    # moment either preference was changed a second time). Readers
    # (`identity/preferences.py`) take the row with the latest
    # `updated_at` for a given key; earlier rows are history, not
    # garbage -- nothing here prunes them. Migrated from the old
    # single-row-per-key shape by `_migrate_preferences` below, not
    # dropped and recreated.
    "preferences": """
        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TEXT NOT NULL
        )
    """,
    "reminders": """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY,
            due_at TEXT NOT NULL,
            text TEXT NOT NULL,
            recurrence TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
    """,
    # engine_ms/first_audio_ms split into per-stage columns (item E) so
    # a slow turn can be traced to the stage that caused it. Migrated
    # from the old shape by `_migrate_turns` below: old rows keep their
    # id/ts/mode/eou_ms/handoff/engine values, with the new granular
    # columns left NULL (a real "we don't have this breakdown for turns
    # logged before this schema existed", not a fabricated split of the
    # old combined engine_ms).
    "turns": """
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            mode TEXT,
            eou_ms INTEGER,
            stt_ms INTEGER,
            first_token_ms INTEGER,
            first_tts_chunk_ms INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            cost_usd REAL,
            handoff INTEGER,
            engine TEXT
        )
    """,
    "initiatives": """
        CREATE TABLE IF NOT EXISTS initiatives (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            reason TEXT,
            source_episode INTEGER REFERENCES episodes(id),
            spoken INTEGER NOT NULL DEFAULT 0,
            suppressed_by TEXT
        )
    """,
}


class UnknownTable(ValueError):
    """Raised for any table name outside `_SCHEMA` — table names come from
    call sites in this codebase, never from user input, but `append`/`read`
    interpolate them into SQL and a typo should fail loudly, not silently
    query nothing or open an injection seam."""


class NotRetirable(ValueError):
    """Raised by `retire()` for a table that exists but has no notion of
    "stopped being true" — `episodes`, `preferences`, `turns`,
    `initiatives`, `entities`. A silent no-op here would be exactly the
    failure `retire` exists to prevent: a caller believing a wrong belief
    was corrected when nothing changed."""


class UnknownRow(LookupError):
    """Raised by `retire()` when `row_id` matches nothing. Loud on purpose:
    a correction aimed at a stale or mistyped id must not look like it
    succeeded. Retiring an already-retired row is *not* an error —
    "this stopped being true" said twice is still true."""

    def __init__(self, table: str, row_id: int) -> None:
        super().__init__(f"no row {row_id} in {table}")
        self.table = table
        self.row_id = row_id


# What `retire` does per table: the SQL that sets the "no longer true"
# marker, the key column, and whether `at` is written. The key is `id`
# where the schema has one and SQLite's implicit `rowid` for `edges`,
# which SPEC.md defines with no primary key. `rules`/`reminders` have no
# column to hold *when* they were retired, so `at` is only written for
# `edges` -- a `retired_at` column is a schema question for SPEC.md, not
# something this primitive invents.
_RETIREMENT: dict[str, tuple[str, str, bool]] = {
    "rules": ("active = 0", "id", False),
    "reminders": ("active = 0", "id", False),
    # COALESCE: the first end date stands. Retiring an already-ended
    # relationship again must not move when it ended, or "idempotent"
    # would be true for the flag tables and false for this one.
    "edges": ("until = COALESCE(until, ?)", "rowid", True),
}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_preferences(conn: sqlite3.Connection) -> None:
    """Old shape: `preferences(key PRIMARY KEY, value, updated_at)`. New:
    `preferences(id, key, value, updated_at)`, no uniqueness on `key`.
    A no-op if the table doesn't exist yet (a fresh install — `create()`'s
    own `CREATE TABLE IF NOT EXISTS` handles that) or already has the new
    shape (`id` present). Renames the old table aside, creates the new
    one, copies every row across by name (nothing is dropped or
    reinterpreted), then drops the renamed original."""
    columns = _existing_columns(conn, "preferences")
    if not columns or "id" in columns:
        return
    conn.execute("ALTER TABLE preferences RENAME TO preferences_pre_migration")
    conn.execute(_SCHEMA["preferences"])
    conn.execute(
        "INSERT INTO preferences (key, value, updated_at) "
        "SELECT key, value, updated_at FROM preferences_pre_migration"
    )
    conn.execute("DROP TABLE preferences_pre_migration")


def _migrate_turns(conn: sqlite3.Connection) -> None:
    """Old shape: `turns(id, ts, mode, eou_ms, engine_ms, first_audio_ms,
    handoff, engine)`. New: `engine_ms`/`first_audio_ms` split into
    per-stage columns (item E). A no-op if the table doesn't exist yet or
    already has the new shape (`stt_ms` present). Old rows keep every
    column that still has a direct equivalent (id, ts, mode, eou_ms,
    handoff, engine) — `stt_ms`/`first_token_ms`/`first_tts_chunk_ms`/
    `prompt_tokens`/`completion_tokens`/`cost_usd` are left `NULL` for
    migrated rows rather than guessed at by splitting the old combined
    `engine_ms` some arbitrary way; `latency_budget.py` already excludes
    `NULL` timing values from its percentiles rather than treating them
    as zero, so this degrades the same way an under-instrumented session
    already does today."""
    columns = _existing_columns(conn, "turns")
    if not columns or "stt_ms" in columns:
        return
    conn.execute("ALTER TABLE turns RENAME TO turns_pre_migration")
    conn.execute(_SCHEMA["turns"])
    conn.execute(
        "INSERT INTO turns (id, ts, mode, eou_ms, handoff, engine) "
        "SELECT id, ts, mode, eou_ms, handoff, engine FROM turns_pre_migration"
    )
    conn.execute("DROP TABLE turns_pre_migration")


class IdentityStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    @property
    def path(self) -> Path:
        """Read-only, purely additive to `create`/`append`/`read` — added
        for `identity/compile.py`'s background-thread refresh
        (`voice/engine/cascade.py`), which needs its own connection to
        the same file rather than sharing this one: `sqlite3` connections
        can't cross threads (`check_same_thread`, on by default and not
        something to silently flip off for one caller's convenience —
        that would change this object's threading contract for
        everyone). A second connection to the same file is SQLite's own
        supported way to do this; this property is what makes opening
        one possible without reaching into `_path` from outside."""
        return self._path

    def create(self) -> None:
        """Create the schema. Idempotent — safe to call on every startup.
        Also migrates `preferences`/`turns` from their pre-2026-09-18
        shapes in place, so an existing device's history survives a
        code update rather than starting over — see
        `_migrate_preferences`/`_migrate_turns`."""
        with self._conn:
            _migrate_preferences(self._conn)
            _migrate_turns(self._conn)
            for statement in _SCHEMA.values():
                self._conn.execute(statement)

    def append(self, table: str, **fields: Any) -> int:
        """Insert one row. Returns its rowid."""
        if table not in _SCHEMA:
            raise UnknownTable(table)
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        cursor = self._conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def read(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        """Select rows, optionally filtered by exact column match."""
        if table not in _SCHEMA:
            raise UnknownTable(table)
        query = f"SELECT * FROM {table}"
        params: tuple[Any, ...] = ()
        if filters:
            query += " WHERE " + " AND ".join(f"{column} = ?" for column in filters)
            params = tuple(filters.values())
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def retire(self, table: str, row_id: int, at: datetime | str) -> None:
        """Mark one row as having stopped being true. `rules` and
        `reminders`: `active` becomes 0. `edges`: `until` becomes `at`.
        Nothing is deleted and nothing else on the row changes — what was
        once believed stays readable (`profile.py`'s `include_inactive`),
        which is the point of retiring rather than deleting.

        `row_id` is the row's `id` for `rules`/`reminders` and its SQLite
        `rowid` for `edges` (no `id` column). `read()` is `SELECT *`,
        which never includes an implicit `rowid`, so today nothing can
        obtain an edge's row id through this interface; the branch exists
        so the primitive is complete, and the gap is recorded in
        `docs/completed/memory.md`.

        Raises `UnknownTable` for a table outside the schema,
        `NotRetirable` for one with nothing to retire, and `UnknownRow`
        when `row_id` matches nothing. Idempotent otherwise.
        """
        if table not in _SCHEMA:
            raise UnknownTable(table)
        if table not in _RETIREMENT:
            raise NotRetirable(table)
        assignment, key_column, takes_at = _RETIREMENT[table]
        at_text = at.isoformat() if isinstance(at, datetime) else str(at)
        params: tuple[Any, ...] = (at_text, row_id) if takes_at else (row_id,)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE {table} SET {assignment} WHERE {key_column} = ?", params
            )
        if cursor.rowcount == 0:
            raise UnknownRow(table, row_id)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "IdentityStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
