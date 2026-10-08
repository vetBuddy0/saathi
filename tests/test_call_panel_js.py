"""saathi/screen/static/js/call-panel.js with the real style.css in a
1920x1080 headless Chromium.

What's pinned: a `call` message draws name, number, status word and (once
connected) a running timer on the right, with the face beside it on the
left -- never hidden; the End call button is big and sends exactly one
`call_hangup` with the call's id; `call: null` clears it and gives the
face its screen back; a malformed message is dropped.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script, stylesheet_link

_SCENARIO = """
import { createCallPanel, formatDuration, statusWord } from "%(panel)s";
const sent = [];
let fakeNow = 1000000;
const panel = createCallPanel((m) => sent.push(m), { now: () => fakeNow });
const out = {};
const text = (sel) => document.querySelector(sel).textContent;
const rect = (el) => {
  const r = el.getBoundingClientRect();
  return { left: r.left, right: r.right, width: r.width, height: r.height };
};

out.durations = [formatDuration(7), formatDuration(83), formatDuration(3729), formatDuration(-5),
  formatDuration("x")];
out.words = ["calling", "ringing", "connected", "?"].map(statusWord);
out.closed = { hidden: document.getElementById("call-panel").hidden,
  open: document.body.classList.contains("call--open") };

panel.onMessage({ type: "call", call: { id: "c1", name: "Priya", number: "+6591234567",
  status: "ringing", elapsed_seconds: null } });
const root = document.getElementById("call-panel");
out.ringing = {
  name: text(".call-panel__name"), number: text(".call-panel__number"),
  status: text(".call-panel__status"), timer: text(".call-panel__timer"),
  panel: rect(root), face: rect(document.getElementById("face-container")),
  button: rect(document.querySelector(".call-panel__end")),
  buttonText: text(".call-panel__end"),
};

panel.onMessage({ type: "call", call: { id: "c1", name: "Priya", number: "+6591234567",
  status: "connected", elapsed_seconds: 65 } });
out.connected_at = text(".call-panel__timer");
fakeNow += 10000;
await new Promise((r) => setTimeout(r, 400));
out.connected_later = text(".call-panel__timer");

document.querySelector(".call-panel__end").click();
document.querySelector(".call-panel__end").click();
out.sent = sent.slice();

panel.onMessage({ type: "call", call: { id: 7 } });
panel.onMessage({ type: "call", call: "nope" });
out.after_malformed = text(".call-panel__name");

panel.onMessage({ type: "call", call: null });
out.cleared = { hidden: root.hidden, open: document.body.classList.contains("call--open"),
  face: rect(document.getElementById("face-container")) };

panel.onMessage({ type: "call", call: { id: "c2", name: "", number: "+6598765432",
  status: "calling", elapsed_seconds: null } });
out.no_name = { name: text(".call-panel__name"), number: text(".call-panel__number") };

document.body.dataset.out = JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def results() -> dict:
    return run_module_script(
        _SCENARIO % {"panel": module_url("call-panel.js")},
        body_html=stylesheet_link() + '<div id="face-container"></div>',
    )


def test_durations_and_status_words(results):
    assert results["durations"] == ["0:07", "1:23", "1:02:09", "0:00", "0:00"]
    assert results["words"] == ["Calling", "Ringing", "Connected", ""]


def test_nothing_is_drawn_before_a_call(results):
    assert results["closed"] == {"hidden": True, "open": False}


def test_a_ringing_call_shows_who_and_their_number_on_the_right(results):
    r = results["ringing"]
    assert (r["name"], r["number"], r["status"], r["timer"]) == (
        "Priya",
        "+6591234567",
        "Ringing",
        "",
    )
    assert r["panel"]["left"] >= 1920 * 0.55 and r["panel"]["right"] == 1920
    # The face is beside it, on the left, and still there.
    assert r["face"]["left"] == 0 and 0 < r["face"]["right"] <= r["panel"]["left"]
    assert r["button"]["height"] >= 112 and r["button"]["width"] >= 300
    assert r["buttonText"] == "End call"


def test_the_timer_counts_locally_from_elapsed_seconds(results):
    assert results["connected_at"] == "1:05"
    assert results["connected_later"] == "1:15"


def test_end_call_sends_one_hangup_with_the_call_id(results):
    assert results["sent"] == [{"type": "call_hangup", "id": "c1"}]


def test_a_malformed_message_is_dropped(results):
    assert results["after_malformed"] == "Priya"


def test_call_null_clears_and_gives_the_face_its_screen_back(results):
    cleared = results["cleared"]
    assert cleared["hidden"] is True and cleared["open"] is False
    assert cleared["face"]["width"] == 1920


def test_with_no_name_the_number_is_the_name(results):
    assert results["no_name"] == {"name": "+6598765432", "number": ""}
