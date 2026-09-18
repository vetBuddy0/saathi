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
    "preferences": """
        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,
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
    "turns": """
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            mode TEXT,
            eou_ms INTEGER,
            engine_ms INTEGER,
            first_audio_ms INTEGER,
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
        """Create the schema. Idempotent — safe to call on every startup."""
        with self._conn:
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
