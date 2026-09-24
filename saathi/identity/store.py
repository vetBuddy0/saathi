"""`IdentityStore` — one of the five interfaces. SQLite, the schema from
SPEC.md, and nothing but `create`, `append`, `read`.

This is deliberately not a repository with one method per table. SPEC.md's
"Memory" section draws the real boundary: *stored* is rows, confidence and
provenance; *sent* is plain sentences, and that translation is
`identity/compile.py`'s job, not this file's. Retrieval scoring
(`reflect.py`), the compiled context block (`compile.py`), and the
family-editable view (`profile.py`) all build on top of `append`/`read` —
none of that domain logic belongs here, or checkpoint 1's "nothing but
create, append and read" stops meaning anything.

One SQLite file on the device, not a service — "the identity file is the
product's asset and lives where the device is" (SPEC.md). `embedding` is
stored as a BLOB; `sqlite-vec` or brute-force numpy cosine (checkpoint 3)
both read it back the same way, so nothing here needs to know which.
"""

from __future__ import annotations

import sqlite3
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
    #
    # voice/tts_chars/tts_cost_usd (2026-09-25): which voice pair spoke
    # the reply, how many characters it synthesized and what that cost,
    # so the Ctrl+L panel can show spend per voice from real turns
    # rather than a list price. `cost_usd` stays the LLM half; nothing
    # sums the two. Added in place by `_migrate_turns_add_voice_usage`;
    # NULL on every row logged before the columns existed.
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
            engine TEXT,
            voice TEXT,
            tts_chars INTEGER,
            tts_cost_usd REAL
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


_TURNS_VOICE_USAGE_COLUMNS = (
    ("voice", "TEXT"),
    ("tts_chars", "INTEGER"),
    ("tts_cost_usd", "REAL"),
)


def _migrate_turns_add_voice_usage(conn: sqlite3.Connection) -> None:
    """2026-09-25: `voice`, `tts_chars`, `tts_cost_usd` added to `turns`
    so the panel's per-voice cost comes from real usage. `ALTER TABLE
    ADD COLUMN`, one per missing column, rather than the rename-and-copy
    `_migrate_turns` needs -- these are purely additive and SQLite adds
    a nullable column in place. Runs *after* `_migrate_turns`, which
    recreates a pre-2026-09-18 table from `_SCHEMA` (already carrying
    these columns), so a v1 file never reaches this step needing
    anything and a v2 file gets exactly the three ALTERs. A no-op when
    the table doesn't exist yet (fresh install: `_SCHEMA` creates it
    whole) or already has `voice`."""
    columns = _existing_columns(conn, "turns")
    if not columns:
        return
    for column, column_type in _TURNS_VOICE_USAGE_COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE turns ADD COLUMN {column} {column_type}")


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
        shapes in place, and `turns` from its pre-2026-09-25 shape, so
        an existing device's history survives a code update rather than
        starting over — see `_migrate_preferences`/`_migrate_turns`/
        `_migrate_turns_add_voice_usage`."""
        with self._conn:
            _migrate_preferences(self._conn)
            _migrate_turns(self._conn)
            _migrate_turns_add_voice_usage(self._conn)
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

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "IdentityStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
