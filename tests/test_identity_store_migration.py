"""identity/store.py's migration from the pre-2026-09-18 schema shapes.
Builds a real SQLite file with the *old* schema by hand (bypassing
IdentityStore entirely, the way an existing device's file actually looks
before a code update), then opens it through IdentityStore and checks
the migration ran correctly and existing rows survived.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from saathi.identity.store import IdentityStore


@pytest.fixture
def old_schema_db_path():
    tmp_dir = tempfile.mkdtemp()
    path = Path(tmp_dir) / "identity.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE turns ("
        "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, mode TEXT, eou_ms INTEGER, "
        "engine_ms INTEGER, first_audio_ms INTEGER, handoff INTEGER, engine TEXT)"
    )
    conn.execute(
        "INSERT INTO preferences (key, value, updated_at) VALUES ('language', 'english', ?)",
        ("2026-09-01T00:00:00+00:00",),
    )
    conn.execute(
        "INSERT INTO turns (ts, mode, eou_ms, engine_ms, first_audio_ms, handoff, engine) "
        "VALUES ('2026-09-01T00:00:00+00:00', 'voice', 900, 800, 1100, 0, 'cascade')"
    )
    conn.commit()
    conn.close()
    return path


def test_preferences_migration_preserves_the_existing_row(old_schema_db_path):
    with IdentityStore(old_schema_db_path) as store:
        store.create()
        rows = store.read("preferences", key="language")
        assert len(rows) == 1
        assert rows[0]["value"] == "english"
        assert rows[0]["updated_at"] == "2026-09-01T00:00:00+00:00"
        assert "id" in rows[0]  # the new column exists


def test_preferences_migration_allows_a_second_write_afterward(old_schema_db_path):
    from saathi.identity.preferences import LANGUAGE_KEY, read_preference, write_preference

    with IdentityStore(old_schema_db_path) as store:
        store.create()
        # The whole point: this used to raise sqlite3.IntegrityError.
        write_preference(store, LANGUAGE_KEY, "chinese")
        assert read_preference(store, LANGUAGE_KEY) == "chinese"
        rows = store.read("preferences", key=LANGUAGE_KEY)
        assert len(rows) == 2  # old row preserved, not overwritten


def test_turns_migration_preserves_existing_columns(old_schema_db_path):
    with IdentityStore(old_schema_db_path) as store:
        store.create()
        rows = store.read("turns")
        assert len(rows) == 1
        row = rows[0]
        assert row["ts"] == "2026-09-01T00:00:00+00:00"
        assert row["mode"] == "voice"
        assert row["eou_ms"] == 900
        assert row["handoff"] == 0
        assert row["engine"] == "cascade"


def test_turns_migration_leaves_new_granular_columns_null_not_guessed(old_schema_db_path):
    with IdentityStore(old_schema_db_path) as store:
        store.create()
        row = store.read("turns")[0]
        # The old row's engine_ms=800/first_audio_ms=1100 have no lossless
        # home in the new stt_ms/first_token_ms/first_tts_chunk_ms split --
        # left NULL rather than guessed at, same as any other
        # under-instrumented turn.
        assert row["stt_ms"] is None
        assert row["first_token_ms"] is None
        assert row["first_tts_chunk_ms"] is None
        assert row["prompt_tokens"] is None
        assert row["completion_tokens"] is None
        assert row["cost_usd"] is None


def test_turns_migration_excludes_migrated_rows_from_the_latency_budget_percentile(
    old_schema_db_path,
):
    from saathi.latency_budget import check_latency_budget

    with IdentityStore(old_schema_db_path) as store:
        store.create()
        result = check_latency_budget(store, engine="cascade")
        # The one migrated row has no granular timings -- it must not be
        # silently counted as a 0ms (or any fabricated-value) turn.
        assert result.turns_checked == 1
        assert result.voice_starts_p95_ms is None
        assert result.brain_finishes_p95_ms is None


def test_create_is_idempotent_after_migration(old_schema_db_path):
    # A second create() call (every startup) must not re-migrate an
    # already-migrated file or duplicate rows.
    with IdentityStore(old_schema_db_path) as store:
        store.create()
        store.create()
        assert len(store.read("preferences")) == 1
        assert len(store.read("turns")) == 1


def test_a_fresh_database_needs_no_migration_and_gets_the_new_schema_directly():
    tmp_dir = tempfile.mkdtemp()
    with IdentityStore(Path(tmp_dir) / "identity.sqlite3") as store:
        store.create()  # nothing to migrate -- must not raise
        row_id = store.append("preferences", key="language", value="english", updated_at="now")
        assert row_id is not None
        turn_id = store.append(
            "turns",
            ts="now",
            mode="voice",
            eou_ms=1,
            stt_ms=2,
            first_token_ms=3,
            first_tts_chunk_ms=4,
            prompt_tokens=5,
            completion_tokens=6,
            cost_usd=0.001,
            handoff=0,
            engine="cascade",
        )
        assert turn_id is not None
