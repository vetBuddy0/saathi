from datetime import datetime, timezone

import pytest

from saathi.identity.store import IdentityStore, NotRetirable, UnknownRow, UnknownTable


@pytest.fixture
def store(tmp_path):
    with IdentityStore(tmp_path / "identity.sqlite3") as store:
        store.create()
        yield store


# -- retire() ----------------------------------------------------------------
#
# The fourth verb (2026-09-25): one narrow "this stopped being true"
# primitive. Not update(): no column name, no value, and it refuses
# tables that have nothing to retire.


def _rule(store, text="a rule", active=1):
    return store.append(
        "rules", text=text, confidence=0.9, learned_at="2026-09-25T00:00:00+00:00",
        source_episode=None, active=active,
    )


def test_retire_a_rule_flips_active_to_zero_and_keeps_the_row(store):
    rule_id = _rule(store, "She has a scan Thursday.")
    store.retire("rules", rule_id, "2026-09-25T10:00:00+00:00")
    rows = store.read("rules", id=rule_id)
    assert len(rows) == 1
    assert rows[0]["active"] == 0
    assert rows[0]["text"] == "She has a scan Thursday."


def test_retire_a_reminder_flips_active_to_zero(store):
    reminder_id = store.append(
        "reminders", due_at="2026-09-25T20:00:00+00:00", text="tablets", recurrence=None, active=1
    )
    store.retire("reminders", reminder_id, "2026-09-25T20:05:00+00:00")
    assert store.read("reminders", id=reminder_id)[0]["active"] == 0


def test_retire_an_edge_sets_until_by_rowid(store):
    a = store.append("entities", kind="person", name="Margaret", notes=None, created_at="now")
    b = store.append("entities", kind="person", name="Priya", notes=None, created_at="now")
    edge_rowid = store.append("edges", src=a, dst=b, relation="daughter", since="2020", until=None)
    store.retire("edges", edge_rowid, datetime(2026, 9, 25, tzinfo=timezone.utc))
    edges = store.read("edges", src=a, dst=b)
    assert len(edges) == 1
    assert edges[0]["until"] == "2026-09-25T00:00:00+00:00"
    assert edges[0]["since"] == "2020"


def test_retire_accepts_a_datetime_or_an_iso_string(store):
    a = store.append("entities", kind="person", name="A", notes=None, created_at="now")
    b = store.append("entities", kind="person", name="B", notes=None, created_at="now")
    first = store.append("edges", src=a, dst=b, relation="x", since=None, until=None)
    second = store.append("edges", src=b, dst=a, relation="y", since=None, until=None)
    store.retire("edges", first, "2026-01-01T00:00:00+00:00")
    store.retire("edges", second, datetime(2026, 1, 2, tzinfo=timezone.utc))
    untils = {e["relation"]: e["until"] for e in store.read("edges")}
    assert untils == {"x": "2026-01-01T00:00:00+00:00", "y": "2026-01-02T00:00:00+00:00"}


def test_retire_is_idempotent(store):
    rule_id = _rule(store)
    store.retire("rules", rule_id, "now")
    store.retire("rules", rule_id, "later")  # "this stopped being true" twice is still true
    assert store.read("rules", id=rule_id)[0]["active"] == 0


def test_retiring_an_ended_edge_again_keeps_the_original_end_date(store):
    a = store.append("entities", kind="person", name="A", notes=None, created_at="now")
    b = store.append("entities", kind="person", name="B", notes=None, created_at="now")
    edge = store.append("edges", src=a, dst=b, relation="carer", since=None, until=None)
    store.retire("edges", edge, "2026-01-01T00:00:00+00:00")
    store.retire("edges", edge, "2026-09-25T00:00:00+00:00")
    assert store.read("edges")[0]["until"] == "2026-01-01T00:00:00+00:00"


def test_retire_only_touches_the_named_row(store):
    keep = _rule(store, "keep me")
    drop = _rule(store, "drop me")
    store.retire("rules", drop, "now")
    assert store.read("rules", id=keep)[0]["active"] == 1
    assert store.read("rules", id=drop)[0]["active"] == 0


def test_retire_an_unknown_row_id_raises_not_silently_no_ops(store):
    with pytest.raises(UnknownRow) as exc_info:
        store.retire("rules", 999, "now")
    assert exc_info.value.table == "rules"
    assert exc_info.value.row_id == 999


def test_retire_an_unknown_table_is_rejected(store):
    with pytest.raises(UnknownTable):
        store.retire("beliefs", 1, "now")


@pytest.mark.parametrize("table", ["episodes", "preferences", "turns", "initiatives", "entities"])
def test_retire_a_table_with_nothing_to_retire_fails_loudly(store, table):
    # Every table in the schema that has no "stopped being true" column.
    # A silent no-op here is the exact failure retire() exists to prevent.
    with pytest.raises(NotRetirable):
        store.retire(table, 1, "now")


def test_a_retired_rule_leaves_the_compiled_context(store, tmp_path):
    from saathi.identity.compile import compile_context

    persona = tmp_path / "persona.txt"
    persona.write_text("You are warm.")
    rule_id = _rule(store, "Her daughter Priya visits on Saturdays.")
    assert "Priya visits" in compile_context(store, base_persona_path=persona)
    store.retire("rules", rule_id, "now")
    assert "Priya visits" not in compile_context(store, base_persona_path=persona)


def test_create_is_idempotent(store):
    store.create()
    store.create()


def test_append_then_read_round_trip(store):
    row_id = store.append(
        "entities", kind="person", name="Priya", notes=None, created_at="2026-01-01T00:00:00"
    )
    rows = store.read("entities", id=row_id)
    assert len(rows) == 1
    assert rows[0]["name"] == "Priya"
    assert rows[0]["kind"] == "person"


def test_read_filters_by_column(store):
    store.append("preferences", key="wake_time", value="07:00", updated_at="2026-01-01")
    store.append("preferences", key="volume", value="loud", updated_at="2026-01-01")

    rows = store.read("preferences", key="wake_time")
    assert [row["value"] for row in rows] == ["07:00"]


def test_read_with_no_filters_returns_everything(store):
    store.append("preferences", key="a", value="1", updated_at="now")
    store.append("preferences", key="b", value="2", updated_at="now")
    assert len(store.read("preferences")) == 2


def test_unknown_table_is_rejected_for_append(store):
    with pytest.raises(UnknownTable):
        store.append("drop_table_entities", x=1)


def test_unknown_table_is_rejected_for_read(store):
    with pytest.raises(UnknownTable):
        store.read("nonexistent")


def test_every_schema_table_is_reachable(store):
    # Not a schema test in disguise for its own sake: this exists so a
    # table added to SPEC.md's schema but never wired into `create()`
    # fails here instead of silently missing at runtime.
    for table in [
        "entities",
        "edges",
        "episodes",
        "rules",
        "preferences",
        "reminders",
        "turns",
        "initiatives",
    ]:
        assert store.read(table) == []
