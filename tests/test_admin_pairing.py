"""The console's "Pair a phone" (screen/admin.py over cloud/pairing.py):
a code, its QR and who has paired, through the real screen server; 409
"Cloud is not set up" with no cloud; and admin.js drawing it in headless
Chromium from the routes' own JSON."""

import json
import re

import pytest
from aiohttp.test_utils import TestClient, TestServer

from saathi.cloud.inbox import Inbox
from saathi.core import Core
from saathi.identity.store import IdentityStore
from saathi.screen.admin import STATIC_DIR
from saathi.screen.server import build_app
from tests.fake_firestore import FakeFirestore


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_DATA_DIR", str(tmp_path))
    path = tmp_path / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
    return path


async def _client(store_path, inbox=None):
    store = IdentityStore(store_path)
    app = build_app(Core(), store=store, inbox=inbox, inbox_tick_seconds=None,
                    reminder_tick_seconds=None)
    return store, TestClient(TestServer(app, host="127.0.0.1"))


async def test_with_no_cloud_pairing_says_so(store_path):
    store, client = await _client(store_path)
    async with client:
        for response in (await client.get("/admin/api/pairing"),
                         await client.post("/admin/api/pairing", json={})):
            assert response.status == 409
            assert await response.text() == "Cloud is not set up"
    store.close()


async def test_pairing_writes_a_code_and_lists_who_claimed_it(store_path):
    firestore = FakeFirestore()
    inbox = Inbox(firestore, "dev1", store_path, device_uid="u-dev")
    store, client = await _client(store_path, inbox)
    async with client:
        made = await client.post("/admin/api/pairing", json={})
        assert made.status == 200
        data = await made.json()
        assert re.fullmatch(r"\d{6}", data["code"])
        assert data["seconds"] == 600 and data["caregivers"] == []
        assert "<svg" in data["qr_svg"]
        assert firestore.docs[f"pairing/{data['code']}"]["deviceId"] == "dev1"
        assert firestore.docs["devices/dev1"]["deviceUid"] == "u-dev"
        # The caregiver's app claims it (the backend's job, here by hand).
        firestore.docs["devices/dev1"]["caregivers"].append("uidPriya")
        listed = await (await client.get("/admin/api/pairing")).json()
        assert listed == {"caregivers": ["uidPriya"]}
        firestore.offline = True
        failed = await client.post("/admin/api/pairing", json={})
        assert failed.status == 502 and "Couldn't reach the cloud" in await failed.text()
    store.close()


def _run_console(data, pairing_post, pairing_get, actions):
    from tests.chromium_harness import run_module_script

    page = (STATIC_DIR / "index.html").read_text()
    body = re.search(r"<body>(.*)<script", page, re.S).group(1)
    script = """
    const fail = (why) => { document.body.dataset.out = JSON.stringify({ error: String(why) }); };
    window.addEventListener("error", (e) => fail(e.message));
    window.addEventListener("unhandledrejection", (e) => fail(e.reason));
    const data = %(data)s;
    const pairingPost = %(post)s;
    const pairingGet = %(get)s;
    const answer = (r) => r.status === 200
      ? { ok: true, json: async () => r.body }
      : { ok: false, statusText: "", text: async () => r.body };
    const posts = [];
    window.fetch = async (url, init = {}) => {
      const path = url.replace("/admin/api/", "");
      if (path === "pairing") {
        if (init.method === "POST") { posts.push(path); return answer(pairingPost); }
        return answer(pairingGet);
      }
      return { ok: true, json: async () => data[path] };
    };
    await import("%(admin)s");
    const wait = () => new Promise((r) => setTimeout(r, 300));
    await wait();
    const $ = (id) => document.getElementById(id);
    const out = { before: $("pair-status").textContent };
    %(actions)s
    out.posts = posts;
    document.body.dataset.out = JSON.stringify(out);
    """ % {
        "data": json.dumps(data).replace("</", "<\\/"),
        "post": json.dumps(pairing_post).replace("</", "<\\/"),
        "get": json.dumps(pairing_get).replace("</", "<\\/"),
        "admin": (STATIC_DIR / "admin.js").resolve().as_uri(),
        "actions": actions,
    }
    return run_module_script(script, body_html=body)


async def _console_json(client) -> dict:
    data = {}
    for name in ("overview", "memory", "prompt", "contacts", "keys", "layout", "reminders"):
        data[name] = await (await client.get(f"/admin/api/{name}")).json()
    return data


async def test_the_pair_button_shows_the_code_its_qr_a_countdown_and_the_phones(store_path):
    firestore = FakeFirestore()
    inbox = Inbox(firestore, "dev1", store_path)
    store, client = await _client(store_path, inbox)
    async with client:
        data = await _console_json(client)
        # One phone paired earlier; a second is being paired now.
        firestore.put("devices/dev1", name="Saathi", caregivers=["uidPriya"])
        listed = await (await client.get("/admin/api/pairing")).json()
        posted = await (await client.post("/admin/api/pairing", json={})).json()
    store.close()
    out = _run_console(
        data,
        {"status": 200, "body": posted},
        {"status": 200, "body": listed},
        """
        out.hidden = $("pairing").hidden;
        out.listedFirst = [...document.querySelectorAll("#caregivers .row__text")].map((e) => e.textContent);
        $("pair").click();
        await wait();
        out.code = $("pair-code").textContent;
        out.qr = $("pair-qr").querySelector("svg") !== null;
        out.left = $("pair-left").textContent;
        out.shown = !$("pairing").hidden;
        out.phones = [...document.querySelectorAll("#caregivers .row__text")].map((e) => e.textContent);
        """,
    )
    assert "error" not in out, out
    assert out["before"] == "" and out["hidden"] is True
    assert out["code"] == posted["code"] and out["qr"] is True and out["shown"] is True
    assert re.fullmatch(r"Expires in (10:00|9:[0-5]\d)", out["left"]), out["left"]
    assert out["listedFirst"] == ["uidPriya"] == out["phones"]
    assert posted["caregivers"] == ["uidPriya"]
    assert out["posts"] == ["pairing"]


async def test_with_no_cloud_the_section_says_so_and_shows_no_code(store_path):
    store, client = await _client(store_path)
    async with client:
        data = await _console_json(client)
        refused = await client.post("/admin/api/pairing", json={})
        status, text = refused.status, await refused.text()
    store.close()
    out = _run_console(
        data,
        {"status": status, "body": text},
        {"status": status, "body": text},
        """
        $("pair").click();
        await wait();
        out.after = $("pair-status").textContent;
        out.hidden = $("pairing").hidden;
        """,
    )
    assert "error" not in out, out
    assert out["before"] == "Cloud is not set up" == out["after"]
    assert out["hidden"] is True
