"""screen/admin.py: the developer console. Reads show what is stored and
what is sent; writes go only through IdentityStore's append/retire and
call/contacts.py's convention; everything is loopback-only."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from saathi.call import contacts
from saathi.core import Core
from saathi.identity import compile as compile_mod
from saathi.identity.store import IdentityStore
from saathi.screen.server import build_app


class FakeSession:
    def __init__(self):
        self.refreshed = 0

    def refresh_context(self):
        self.refreshed += 1

    def describe(self):
        return {"provider": "openai", "llm_model": "m", "stt_model": "s", "tools": ["play_music"],
                "voice": "piper"}

    def conversation_snapshot(self):
        return {"summary": "They talked about tea.", "window": [{"her": "hi", "saathi": "hello"}]}


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_DATA_DIR", str(tmp_path))
    path = tmp_path / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
        source = store.append("episodes", ts="2026-10-01T00:00:00+00:00", entity_id=None,
                              text="Admission note.", importance=0.9, embedding=None)
        store.append("rules", text="She likes Teresa Teng.", confidence=0.8,
                     learned_at="2026-10-01T00:00:00+00:00", source_episode=source, active=1)
    return path


@pytest.fixture
async def console(store_path):
    session = FakeSession()
    with IdentityStore(store_path) as store:
        app = build_app(Core(), session=None, store=store,
                        admin_info=lambda: {"calls": "off", "notes": ["n1"]})
        # The console's session is the one build_app was given; use the
        # fake for refresh/describe by building the console directly too.
        from saathi.screen.admin import Console, add_routes
        from aiohttp import web

        app2 = web.Application()
        add_routes(app2, Console(store_path, session=session, info=lambda: {"calls": "off"}))
        async with TestClient(TestServer(app2, host="127.0.0.1")) as client, \
                TestClient(TestServer(app, host="127.0.0.1")) as real:
            yield client, session, real


async def test_the_page_and_every_read_answer(console):
    client, _session, real = console
    assert (await real.get("/admin")).status == 200  # wired into the real screen server
    page = await client.get("/admin")
    assert page.status == 200 and "Saathi Console" in await page.text()
    overview = await (await client.get("/admin/api/overview")).json()
    assert overview["counts"]["rules"] == 1 and overview["engine"]["llm_model"] == "m"
    memory = await (await client.get("/admin/api/memory")).json()
    assert memory["rules"][0]["source"] == "Admission note."
    assert memory["episodes"][0]["sent"] is True
    assert memory["short_term"]["summary"] == "They talked about tea."
    prompt = await (await client.get("/admin/api/prompt")).json()
    assert "[memory 1] She likes Teresa Teng." in prompt["compiled"]
    assert prompt["persona_source"] == "shipped"


async def test_editing_a_rule_retires_the_old_and_appends_the_new_with_a_source(
    console, store_path
):
    client, session, _ = console
    response = await client.post("/admin/api/rules/1", json={"text": "She loves Teresa Teng."})
    assert response.status == 200
    with IdentityStore(store_path) as store:
        rules = {r["id"]: r for r in store.read("rules")}
        assert rules[1]["active"] == 0 and rules[1]["text"] == "She likes Teresa Teng."
        new = rules[(await response.json())["id"]]
        assert new["active"] == 1 and new["text"] == "She loves Teresa Teng."
        source = next(e for e in store.read("episodes") if e["id"] == new["source_episode"])
        assert source["text"].endswith("She loves Teresa Teng.")
    assert session.refreshed == 1
    assert (await client.post("/admin/api/rules/99", json={"text": "x"})).status == 404


async def test_add_and_retire_rules_and_add_an_episode(console, store_path):
    client, _, _ = console
    added = await (await client.post("/admin/api/rules", json={"text": "Call her Auntie."})).json()
    assert (await client.post(f"/admin/api/rules/{added['id']}/retire", json={})).status == 200
    assert (await client.post("/admin/api/episodes", json={"text": "Udhi visited.",
                                                            "importance": 0.7})).status == 200
    too_important = {"text": "x", "importance": 5}
    assert (await client.post("/admin/api/episodes", json=too_important)).status == 400
    assert (await client.post("/admin/api/rules", json={"text": "  "})).status == 400
    with IdentityStore(store_path) as store:
        assert not next(r for r in store.read("rules") if r["id"] == added["id"])["active"]
        assert any(e["text"] == "Udhi visited." for e in store.read("episodes"))


async def test_the_persona_edit_wins_and_reset_brings_back_the_shipped_one(console, store_path):
    client, _, _ = console
    renamed = {"text": "Your name is Meimei."}
    assert (await client.put("/admin/api/persona", json=renamed)).status == 200
    with IdentityStore(store_path) as store:
        assert compile_mod.compile_context(store).startswith("Your name is Meimei.")
    prompt = await (await client.get("/admin/api/prompt")).json()
    assert prompt["persona_source"] == "edited"
    assert (await client.delete("/admin/api/persona")).status == 200
    with IdentityStore(store_path) as store:
        assert not compile_mod.compile_context(store).startswith("Your name is Meimei.")


async def test_contacts_save_edit_and_remove_through_the_contacts_convention(console, store_path):
    client, _, _ = console
    son_row = {"name": "Udhi", "phone": "+65 8961 4304", "relation": "son"}
    ok = await client.post("/admin/api/contacts", json=son_row)
    assert ok.status == 200
    son = contacts.find_by_relation(store_path, "my son")
    assert son.name == "Udhi" and son.phone == "+6589614304"
    # A local number with the country picked.
    await client.post("/admin/api/contacts", json={"name": "Udhi", "phone": "8123 4567",
                                                   "country": "SG", "relation": "son"})
    assert contacts.find_by_exact_name(store_path, "Udhi").phone == "+6581234567"
    bad = await client.post("/admin/api/contacts", json={"name": "X", "phone": "123"})
    assert bad.status == 400
    listed = await (await client.get("/admin/api/contacts")).json()
    assert [c["name"] for c in listed] == ["Udhi"]
    assert (await client.post("/admin/api/contacts/remove", json={"name": "Udhi"})).status == 200
    assert contacts.find_by_relation(store_path, "son") is None
    assert (await client.post("/admin/api/contacts/remove", json={"name": "Udhi"})).status == 404


async def test_keys_show_status_never_values_and_settings_write_preferences(
    console, store_path, monkeypatch
):
    client, _, _ = console
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    response = await client.get("/admin/api/keys")
    text = await response.text()
    assert "sk-secret-value" not in text
    keys = {k["name"]: k["set"] for k in await response.json()}
    assert keys["OPENAI_API_KEY"] is True and keys["YOUTUBE_API_KEY"] is False
    assert (await client.post("/admin/api/preferences",
                              json={"key": "language", "value": "chinese"})).status == 200
    assert (await client.post("/admin/api/preferences",
                              json={"key": "secret", "value": "x"})).status == 400
    memory = await (await client.get("/admin/api/memory")).json()
    assert memory["preferences"]["language"] == "chinese"


async def test_another_machine_on_the_network_is_refused(store_path):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from saathi.screen.admin import Console, add_routes

    app = web.Application()
    add_routes(app, Console(store_path))
    request = make_mocked_request("GET", "/admin/api/keys", app=app,
                                  transport=_Transport(("192.168.1.20", 5000)))
    match = await app.router.resolve(request)
    with pytest.raises(web.HTTPForbidden):
        await match.handler(request)


class _Transport:
    def __init__(self, peer):
        self._peer = peer

    def get_extra_info(self, name, default=None):
        return self._peer if name == "peername" else default


async def test_under_the_hood_reads_the_real_schema_and_numbers(console):
    from saathi.identity import compile as compile_mod
    from saathi.voice import conversation

    client, _, _ = console
    layout = await (await client.get("/admin/api/layout")).json()
    tables = {t["name"]: t for t in layout["tables"]}
    # Columns come from the store's own schema, rows from the database.
    assert tables["rules"]["columns"] == [
        "id", "text", "confidence", "learned_at", "source_episode", "active",
    ]
    assert tables["rules"]["rows"] == 1 and tables["episodes"]["rows"] == 1
    assert all(t["purpose"] for t in layout["tables"])  # every table explained
    assert layout["retrieval"]["top_k"] == compile_mod._TOP_K_EPISODES
    assert layout["window"]["exchanges"] == conversation.MAX_EXCHANGES
    assert "happy" in layout["moods"]
    assert layout["files"]["persona_edited"] is None


async def test_a_setting_saved_in_the_console_reaches_the_open_face_at_once(console):
    # Captions switched on from the tablet's console show on the face
    # without waiting for it to reconnect.
    _client, _session, real = console
    face = await real.ws_connect("/ws")
    try:
        first = await _next_settings(face)
        assert first["captions"] is False
        ok = await real.post("/admin/api/preferences", json={"key": "captions", "value": "on"})
        assert ok.status == 200
        assert (await _next_settings(face))["captions"] is True
    finally:
        await face.close()


async def _next_settings(ws) -> dict:
    import asyncio

    while True:
        message = await asyncio.wait_for(ws.receive_json(), 5)
        if message.get("type") == "settings":
            return message


async def test_reminders_are_listed_added_edited_and_cancelled(console, store_path):
    client, _, _ = console
    made = await client.post("/admin/api/reminders",
                             json={"text": "take your tablet", "time": "09:00", "repeat": "daily"})
    assert made.status == 200
    first = (await made.json())["id"]
    listed = await (await client.get("/admin/api/reminders")).json()
    (row,) = listed["reminders"]
    assert (row["text"], row["repeat"], row["time"]) == ("take your tablet", "daily", "09:00")
    assert row["next"].endswith("at 09:00")
    assert (listed["repeat_minutes"], listed["tries"]) == (10, 3)

    # Editing replaces it: the old row is retired, not rewritten.
    edited = await client.post("/admin/api/reminders", json={
        "text": "take two tablets", "time": "08:30", "repeat": "weekdays", "replaces": first})
    second = (await edited.json())["id"]
    with IdentityStore(store_path) as store:
        assert store.read("reminders", id=first)[0]["active"] == 0
        assert store.read("reminders", id=second)[0]["recurrence"] == "weekdays"

    # No time, a date in the past, an unknown repeat, a stale edit: refused.
    for bad in ({"text": "x", "time": ""}, {"text": "x", "time": "09:00", "date": "2020-01-01"},
                {"text": "x", "time": "09:00", "repeat": "hourly"}):
        assert (await client.post("/admin/api/reminders", json=bad)).status == 400
    stale = {"text": "x", "time": "09:00", "replaces": first}
    assert (await client.post("/admin/api/reminders", json=stale)).status == 404

    assert (await client.post(f"/admin/api/reminders/{second}/cancel", json={})).status == 200
    assert (await client.post(f"/admin/api/reminders/{second}/cancel", json={})).status == 404
    assert (await (await client.get("/admin/api/reminders")).json())["reminders"] == []


async def test_how_often_a_reminder_repeats_is_a_setting(console, store_path):
    from saathi.initiative import reminders

    client, _, _ = console
    for key, good, bad in (("reminder_repeat_minutes", "15", "7"), ("reminder_tries", "5", "9")):
        for value, status in ((bad, 400), (good, 200)):
            sent = await client.post("/admin/api/preferences", json={"key": key, "value": value})
            assert sent.status == status
    with IdentityStore(store_path) as store:
        assert reminders.settings(store) == (reminders.timedelta(minutes=15), 5)
    listed = await (await client.get("/admin/api/reminders")).json()
    assert (listed["repeat_minutes"], listed["tries"]) == (15, 5)


async def test_the_reminders_section_draws_and_sends_what_was_edited(console):
    """admin.js in headless Chromium, fed the console's own JSON."""
    import json
    import re

    from saathi.screen.admin import STATIC_DIR
    from tests.chromium_harness import run_module_script

    client, _, _ = console
    await client.post("/admin/api/reminders",
                      json={"text": "take your tablet", "time": "09:00", "repeat": "daily"})
    data = {}
    for name in ("overview", "memory", "prompt", "contacts", "keys", "layout", "reminders"):
        data[name] = await (await client.get(f"/admin/api/{name}")).json()
    page = (STATIC_DIR / "index.html").read_text()
    body = re.search(r"<body>(.*)<script", page, re.S).group(1)
    script = """
    const fail = (why) => { document.body.dataset.out = JSON.stringify({ error: String(why) }); };
    window.addEventListener("error", (e) => fail(e.message));
    window.addEventListener("unhandledrejection", (e) => fail(e.reason));
    const data = %(data)s;
    const posts = [];
    window.fetch = async (url, init = {}) => {
      const path = url.replace("/admin/api/", "");
      if (init.method === "POST") {
        posts.push([path, JSON.parse(init.body)]);
        return { ok: true, json: async () => ({ ok: true }) };
      }
      return { ok: true, json: async () => data[path] };
    };
    window.confirm = () => true;
    await import("%(admin)s");
    // Waits for what is being asserted, not a fixed time: the harness's
    // virtual-time budget is shared with the console's own timers.
    const until = async (ok) => {
      for (let i = 0; i < 100 && !ok(); i += 1) await new Promise((r) => setTimeout(r, 10));
    };
    await until(() => document.querySelector("#reminders tbody tr"));
    const $ = (id) => document.getElementById(id);
    const out = {};
    out.rows = [...document.querySelectorAll("#reminders tbody tr")]
      .map((tr) => [...tr.children].slice(0, 2).map((td) => td.textContent));
    const repeat = $("reminder-repeat");
    out.repeat = [repeat.value, repeat.options.length, $("reminder-tries").value];
    document.querySelector("#reminders tbody button").click();  // Edit
    const f = $("reminder-form");
    out.form = [f.text.value, f.time.value, f.repeat.value, f.replaces.value !== "",
                $("reminder-save").textContent];
    f.text.value = "take two tablets";
    f.requestSubmit();
    await until(() => posts.length >= 1);
    document.querySelectorAll("#reminders tbody button")[1].click();  // Cancel
    $("reminder-repeat").value = "15";
    $("reminder-repeat").dispatchEvent(new Event("change"));
    await until(() => posts.length >= 3);
    out.posts = posts;
    document.body.dataset.out = JSON.stringify(out);
    """ % {
        "data": json.dumps(data).replace("</", "<\\/"),
        "admin": (STATIC_DIR / "admin.js").resolve().as_uri(),
    }
    out = run_module_script(script, body_html=body)
    assert "error" not in out, out
    assert out["rows"] == [["take your tablet", "Every day, 09:00"]]
    assert out["repeat"] == ["10", 4, "3"]
    assert out["form"] == ["take your tablet", "09:00", "daily", True, "Save changes"]
    (save, cancel, setting) = out["posts"]
    assert save[0] == "reminders" and save[1]["text"] == "take two tablets" and save[1]["replaces"]
    assert cancel[0].endswith("/cancel")
    assert setting == ["preferences", {"key": "reminder_repeat_minutes", "value": "15"}]
