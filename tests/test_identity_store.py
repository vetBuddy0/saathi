import pytest

from saathi.identity.store import IdentityStore, UnknownTable


@pytest.fixture
def store(tmp_path):
    with IdentityStore(tmp_path / "identity.sqlite3") as store:
        store.create()
        yield store


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
