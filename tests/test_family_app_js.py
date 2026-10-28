"""saathi/screen/static/family/static/app.js in a headless Chromium, with
the real family page's markup, a fake WebSocket and a stubbed fetch.

What's pinned: pairing a phone that is already paired (its socket open
and logged in as its old member) says hello again as the NEW member.
Before 2026-10-28 `connect()` kept the open socket, so the phone stayed
the old member and her calls to the new one ended "unreachable" after a
flash of the call panel. (A relink needs no test of its own: it runs at
load, before any socket exists.)
"""

import re
from pathlib import Path

from tests.chromium_harness import run_module_script

FAMILY = Path(__file__).parent.parent / "saathi" / "screen" / "static" / "family"


def _body() -> str:
    page = (FAMILY / "index.html").read_text()
    body = re.search(r"<body[^>]*>(.*)</body>", page, re.S).group(1)
    return re.sub(r"<script.*?</script>", "", body, flags=re.S)


_SCENARIO = """
const hellos = [];
const sockets = [];
class FakeSocket extends EventTarget {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  constructor(url) {
    super();
    this.readyState = 0;
    sockets.push(this);
    setTimeout(() => { this.readyState = 1; this.dispatchEvent(new Event("open")); }, 0);
  }
  send(data) {
    const m = JSON.parse(data);
    if (m.type === "hello") hellos.push(m.member);
  }
  close() {
    this.readyState = 3;
    const e = new Event("close"); e.code = 1000;
    this.dispatchEvent(e);
  }
}
window.WebSocket = FakeSocket;
const reply = {
  ok: true, member_id: "%(new)s", key: "k2", name: "Asha", calls_her: "Mum",
  vapid_public_key: null,
};
window.fetch = async () => ({ json: async () => reply });
localStorage.setItem("saathi-family", JSON.stringify({ member: "old", key: "k1" }));
history.replaceState(null, "", location.pathname + "#%(hash)s=tok");
await import("%(app)s");
const tick = () => new Promise((r) => setTimeout(r, 50));
await tick();
%(act)s
await tick(); await tick();
document.body.dataset.out = JSON.stringify({
  hellos,
  open: sockets.filter((s) => s.readyState === 1).length,
  saved: JSON.parse(localStorage.getItem("saathi-family")).member,
});
"""


def _run(hash_key: str, act: str, new: str) -> dict:
    return run_module_script(
        _SCENARIO
        % {
            "app": (FAMILY / "static" / "app.js").resolve().as_uri(),
            "hash": hash_key,
            "act": act,
            "new": new,
        },
        body_html=_body(),
    )


def test_pairing_an_already_paired_phone_logs_its_socket_in_as_the_new_member():
    out = _run(
        "pair",
        """document.getElementById("pair-name").value = "Asha";
document.getElementById("pair-form").requestSubmit();""",
        "new-member",
    )
    assert out["saved"] == "new-member"
    assert out["hellos"] == ["old", "new-member"]
    assert out["open"] == 1

