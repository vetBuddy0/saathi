"""saathi/screen/static/js/media-panel.js — the panel's DOM and player
logic, driven in a headless Chromium against a stubbed `window.YT`.

What this pins, each found or nearly found in review: the iframe is
created once and never detached (detaching reloads the first video and
orphans the wrapper); a new search does not lose the results to a
stray ENDED from stopVideo(); a play before the API is ready is picked
up on ready, not swallowed; the first video does not start before its
volume is set; the face is in every layout; and the ducking follows
core's state.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script

_STUB_YT = """
window.calls = [];
window.YT = {
  Player: class {
    constructor(el, opts) {
      this.el = el;
      this.events = opts.events;
      window.calls.push(["new", el.tagName, el.src]);
      setTimeout(() => this.events.onReady({ target: this }), 0);
    }
    loadVideoById(id) { window.calls.push(["loadVideoById", id]); }
    playVideo() { window.calls.push(["playVideo"]); }
    pauseVideo() { window.calls.push(["pauseVideo"]); }
    stopVideo() { window.calls.push(["stopVideo"]); }
    setVolume(v) { window.calls.push(["setVolume", v]); }
    fireEnded() { this.events.onStateChange({ data: 0 }); }
    fireError() { this.events.onError({ data: 150 }); }
  },
};
"""

_COMMON = """
const tick = () => new Promise((r) => setTimeout(r, 0));
const out = {};
const bodyClasses = () => Array.from(document.body.classList);
const playMsg = (id, title, index, volume = 70) =>
  ({ type: "media", action: "play", video_id: id, title, index, volume, fullscreen: false });
"""

_SCENARIO = (
    _STUB_YT
    + """
import { createMediaPanel } from "%(panel)s";
const sent = [];
const panel = createMediaPanel((m) => sent.push(m));
"""
    + _COMMON
    + """
const results = [
  { index: 1, label: "One", title: "A", video_id: "vidA" },
  { index: 2, label: "Two", title: "B", video_id: "vidB" },
  { index: 3, label: "Three", title: "C", video_id: "vidC" },
];

panel.onConnected();
out.reset_on_load = sent.slice();

panel.onMessage({ type: "state", state: "idle" });
panel.onMessage({ type: "media", action: "results", query: "q", results });
out.results_classes = bodyClasses();
out.results_text = Array.from(document.querySelectorAll(".media-result"))
  .map((e) => e.textContent);
out.face_present = document.getElementById("face") !== null;

panel.onMessage({ type: "state", state: "speaking" });
panel.onMessage(playMsg("vidA", "A", 1));
const iframe = document.querySelector("iframe.media-player__iframe");
out.iframe_src = iframe.src;
out.iframe_allow = iframe.getAttribute("allow");
out.calls_before_ready = window.calls.slice();
await tick(); await tick();
out.calls_after_ready = window.calls.slice();
out.player_classes = bodyClasses();
out.title_text = document.querySelector(".media-title").textContent;

panel.onMessage({ type: "state", state: "idle" });
out.restored = window.calls[window.calls.length - 1];
panel.onMessage({ type: "state", state: "listening" });
out.ducked = window.calls[window.calls.length - 1];
panel.onMessage({ type: "media", action: "volume", level: 40 });
out.quieter_while_listening = window.calls[window.calls.length - 1];

// A new search while playing: results drawn, video stopped (the stray
// ENDED that stop can surface is covered by the _ENDED_SCENARIO).
window.calls.length = 0;
panel.onMessage({ type: "media", action: "results", query: "q2", results });
out.stop_on_search = window.calls.slice();
out.results_visible_after_stop = !document.querySelector(".media-results").hidden;

// "the second one": the iframe is the same node, not a reloaded one
window.calls.length = 0;
panel.onMessage(playMsg("vidB", "B", 2, 40));
out.same_iframe = document.querySelector("iframe.media-player__iframe") === iframe;
out.second_play_calls = window.calls.slice();
out.iframe_count = document.querySelectorAll("iframe").length;

panel.onMessage({ type: "media", action: "layout", mode: "fullscreen" });
out.fullscreen_classes = bodyClasses();
panel.onMessage({ type: "media", action: "layout", mode: "panel" });
out.panel_classes = bodyClasses();

panel.onMessage({ type: "media", action: "stop" });
out.stopped_classes = bodyClasses();
out.iframe_kept_after_stop = document.querySelectorAll("iframe").length;

panel.onConnected();
out.no_reset_with_player = sent.filter((m) => m.event === "reset").length;
out.sent = sent;
document.body.dataset.out = JSON.stringify(out);
"""
)

_ENDED_SCENARIO = (
    _STUB_YT
    + """
import { createMediaPanel } from "%(panel)s";
const sent = [];
let last = null;
const Orig = window.YT.Player;
window.YT.Player = class extends Orig { constructor(el, o) { super(el, o); last = this; } };
const panel = createMediaPanel((m) => sent.push(m));
"""
    + _COMMON
    + """
const results = [{ index: 1, label: "One", title: "A", video_id: "vidA" }];
panel.onMessage({ type: "media", action: "results", results });
panel.onMessage(playMsg("vidA", "A", 1));
await tick(); await tick();
// new search, then the late ENDED that stopVideo() can surface
panel.onMessage({ type: "media", action: "results", results });
last.fireEnded();
out.results_survive_stray_ended = !document.querySelector(".media-results").hidden;
out.no_ended_sent = sent.filter((m) => m.event === "ended").length;
// now a real ending, while watching
panel.onMessage(playMsg("vidA", "A", 1));
last.fireEnded();
out.ended_sent = sent.filter((m) => m.event === "ended").length;
out.classes_after_ended = Array.from(document.body.classList);
// an unplayable video reports which one
panel.onMessage(playMsg("vidA", "A", 1));
last.fireError();
out.error_sent = sent.filter((m) => m.event === "error");
document.body.dataset.out = JSON.stringify(out);
"""
)

_PENDING_SCENARIO = (
    _STUB_YT
    + """
import { createMediaPanel } from "%(panel)s";
// The API attaches slowly: two plays arrive before onReady. The second
// must win, once, and nothing must be swallowed.
let ready = null;
const Orig = window.YT.Player;
window.YT.Player = class extends Orig {
  constructor(el, o) {
    const onReady = (e) => { ready = () => o.events.onReady(e); };
    super(el, { events: { ...o.events, onReady } });
  }
};
const panel = createMediaPanel(() => {});
"""
    + _COMMON
    + """
panel.onMessage(playMsg("vidA", "A", 1));
panel.onMessage(playMsg("vidB", "B", 2));
await tick(); await tick();
out.iframes = document.querySelectorAll("iframe").length;
out.before_ready = window.calls.filter((c) => c[0] !== "new");
ready();
out.after_ready = window.calls.filter((c) => c[0] !== "new");
panel.onMessage(playMsg("vidC", "C", 3));
out.third = window.calls.slice(-3);
document.body.dataset.out = JSON.stringify(out);
"""
)

_BODY = '<div id="face"></div>'


@pytest.fixture(scope="module")
def scenario() -> dict:
    return run_module_script(_SCENARIO % {"panel": module_url("media-panel.js")}, body_html=_BODY)


@pytest.fixture(scope="module")
def ended() -> dict:
    return run_module_script(_ENDED_SCENARIO % {"panel": module_url("media-panel.js")})


@pytest.fixture(scope="module")
def pending() -> dict:
    return run_module_script(_PENDING_SCENARIO % {"panel": module_url("media-panel.js")})


def test_a_fresh_page_tells_the_server_it_has_no_player_and_only_then(scenario):
    assert scenario["reset_on_load"] == [{"type": "media_event", "event": "reset"}]
    assert scenario["no_reset_with_player"] == 1  # not sent again once a player exists


def test_results_are_drawn_beside_the_face_with_the_spoken_labels(scenario):
    assert scenario["results_classes"] == ["media--panel"]
    assert scenario["results_text"] == ["One: A", "Two: B", "Three: C"]
    assert scenario["face_present"] is True


def test_the_first_video_does_not_start_before_its_volume_is_set(scenario):
    assert "autoplay" not in scenario["iframe_src"]
    assert "enablejsapi=1" in scenario["iframe_src"]
    assert "autoplay" in scenario["iframe_allow"]
    # No volume or play call before onReady (the wrapper's own
    # construction is a microtask after the message, so it may or may
    # not have happened yet at this snapshot -- either way, nothing
    # played)...
    assert all(c[0] == "new" for c in scenario["calls_before_ready"])
    # ...then volume first (ducked: core was SPEAKING), then play.
    after = [c for c in scenario["calls_after_ready"] if c[0] != "new"]
    assert after == [["setVolume", 14], ["playVideo"]]
    assert scenario["player_classes"] == ["media--panel"]
    assert scenario["title_text"] == "A"


def test_ducking_follows_core_state_and_a_volume_change_stays_ducked(scenario):
    assert scenario["restored"] == ["setVolume", 70]
    assert scenario["ducked"] == ["setVolume", 14]
    assert scenario["quieter_while_listening"] == ["setVolume", 8]


def test_a_new_search_stops_the_video_and_keeps_the_results_on_screen(scenario, ended):
    assert scenario["stop_on_search"] == [["stopVideo"]]
    assert scenario["results_visible_after_stop"] is True
    assert ended["results_survive_stray_ended"] is True
    assert ended["no_ended_sent"] == 0


def test_the_second_one_reuses_the_same_iframe_rather_than_reloading_it(scenario):
    assert scenario["same_iframe"] is True
    assert scenario["iframe_count"] == 1
    assert scenario["second_play_calls"] == [
        ["loadVideoById", "vidB"],
        ["setVolume", 8],
        ["playVideo"],
    ]


def test_fullscreen_and_back_and_stop_keep_the_face_and_the_iframe(scenario):
    assert scenario["fullscreen_classes"] == ["media--fullscreen"]
    assert scenario["panel_classes"] == ["media--panel"]
    assert scenario["stopped_classes"] == []
    assert scenario["iframe_kept_after_stop"] == 1


def test_a_real_ending_is_reported_and_clears_the_panel(ended):
    assert ended["ended_sent"] == 1
    assert ended["classes_after_ended"] == []


def test_an_unplayable_video_is_reported_with_its_id(ended):
    assert ended["error_sent"] == [{"type": "media_event", "event": "error", "video_id": "vidA"}]


def test_plays_before_the_api_is_ready_are_not_swallowed(pending):
    assert pending["iframes"] == 1
    assert pending["before_ready"] == []
    assert pending["after_ready"] == [["setVolume", 70], ["loadVideoById", "vidB"]]
    assert pending["third"] == [["loadVideoById", "vidC"], ["setVolume", 70], ["playVideo"]]
