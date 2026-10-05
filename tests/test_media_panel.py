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

_OFFLINE_SCENARIO = """
import { createMediaPanel } from "%(panel)s";
// No window.YT stub and an API script that cannot load: the first play
// must fail out loud (media_event error) and free the panel for the
// next play, not queue every later play behind a promise that never
// resolves (found in review).
const sent = [];
const panel = createMediaPanel((m) => sent.push(m), { iframeApiSrc: "%(missing)s" });
const out = {};
const until = (pred) => new Promise((r) => {
  const tick = () => (pred() ? r() : setTimeout(tick, 5));
  tick();
});
panel.onMessage({ type: "media", action: "play", video_id: "vidA", title: "A", index: 1,
  volume: 70, fullscreen: false });
await until(() => sent.some((m) => m.event === "error"));
out.error_sent = sent.filter((m) => m.event === "error");
out.classes_after = Array.from(document.body.classList);
out.iframes_after = document.querySelectorAll("iframe").length;
// A later play tries again from scratch rather than being swallowed.
window.YT = {
  Player: class {
    constructor(el, o) { setTimeout(() => o.events.onReady({ target: this }), 0); }
    setVolume() {}
    playVideo() { window.played = true; }
    loadVideoById() {}
  },
};
panel.onMessage({ type: "media", action: "play", video_id: "vidB", title: "B", index: 2,
  volume: 70, fullscreen: false });
await until(() => window.played === true);
out.second_play_started = window.played === true;
out.iframes_now = document.querySelectorAll("iframe").length;
document.body.dataset.out = JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def offline() -> dict:
    return run_module_script(
        _OFFLINE_SCENARIO
        % {"panel": module_url("media-panel.js"), "missing": module_url("does-not-exist.js")}
    )


def test_an_api_script_that_fails_to_load_reports_an_error_and_frees_the_panel(offline):
    assert offline["error_sent"] == [
        {"type": "media_event", "event": "error", "video_id": "vidA", "code": "api"}
    ]
    assert offline["classes_after"] == []
    assert offline["iframes_after"] == 0
    assert offline["second_play_started"] is True
    assert offline["iframes_now"] == 1


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
    assert ended["error_sent"] == [
        {"type": "media_event", "event": "error", "video_id": "vidA", "code": 150}
    ]


def test_plays_before_the_api_is_ready_are_not_swallowed(pending):
    assert pending["iframes"] == 1
    assert pending["before_ready"] == []
    assert pending["after_ready"] == [["setVolume", 70], ["loadVideoById", "vidB"]]
    assert pending["third"] == [["loadVideoById", "vidC"], ["setVolume", 70], ["playVideo"]]


# -- a player that never becomes usable fails out loud (2026-09-26) ---------
#
# The kiosk's report was "the play command reaches the page but no iframe
# is visible". Two ways that happened silently: the API script arrived
# but never announced itself, and the wrapper never called onReady --
# `attaching` then stayed true and every later play queued behind it.
# Both now end in a `media_event` error with a code, and the next play
# starts fresh.

_STUCK_PLAYER_SCENARIO = """
import { createMediaPanel } from "%(panel)s";
// A wrapper that never becomes ready.
window.YT = { Player: class { constructor() {} setVolume() {} playVideo() {} } };
const sent = [];
const panel = createMediaPanel((m) => sent.push(m), { readyTimeoutMs: 50 });
const out = {};
const until = (pred) => new Promise((r) => {
  const tick = () => (pred() ? r() : setTimeout(tick, 5));
  tick();
});
const play = (id) => panel.onMessage({ type: "media", action: "play", video_id: id, title: id,
  index: 1, volume: 70, fullscreen: false });
play("vidA");
out.iframe_while_waiting = document.querySelectorAll("iframe").length;
await until(() => sent.some((m) => m.event === "error"));
out.error_sent = sent.filter((m) => m.event === "error");
out.classes_after = Array.from(document.body.classList);
out.iframes_after = document.querySelectorAll("iframe").length;
// A working wrapper now: the next play is not queued behind the dead one.
window.YT = {
  Player: class {
    constructor(el, o) { setTimeout(() => o.events.onReady({ target: this }), 0); }
    setVolume() {}
    playVideo() { window.played = true; }
    loadVideoById() {}
  },
};
play("vidB");
await until(() => window.played === true);
out.second_play_started = true;
out.iframes_now = document.querySelectorAll("iframe").length;
document.body.dataset.out = JSON.stringify(out);
"""

_MUTE_API_SCENARIO = """
import { createMediaPanel } from "%(panel)s";
// An API script that loads (a real file) but never calls
// onYouTubeIframeAPIReady -- a captive portal's page, a partial download.
const sent = [];
const panel = createMediaPanel((m) => sent.push(m), { iframeApiSrc: "%(mute)s", apiTimeoutMs: 50 });
const out = {};
const until = (pred) => new Promise((r) => {
  const tick = () => (pred() ? r() : setTimeout(tick, 5));
  tick();
});
panel.onMessage({ type: "media", action: "play", video_id: "vidA", title: "A", index: 1,
  volume: 70, fullscreen: false });
await until(() => sent.some((m) => m.event === "error"));
out.error_sent = sent.filter((m) => m.event === "error");
out.iframes_after = document.querySelectorAll("iframe").length;
out.classes_after = Array.from(document.body.classList);
document.body.dataset.out = JSON.stringify(out);
"""

_ERROR_BEFORE_READY_SCENARIO = (
    _STUB_YT
    + """
import { createMediaPanel } from "%(panel)s";
// Embedding disabled: some embeds error before they are ever ready.
const Orig = window.YT.Player;
let constructions = 0;
window.YT.Player = class extends Orig {
  constructor(el, o) {
    constructions += 1;
    if (constructions === 1) {
      // The first embed errors before it is ready; later ones are fine.
      super(el, { events: { ...o.events, onReady: () => {} } });
      setTimeout(() => o.events.onError({ data: 150 }), 0);
    } else {
      super(el, o);
    }
  }
};
const sent = [];
const panel = createMediaPanel((m) => sent.push(m));
"""
    + _COMMON
    + """
panel.onMessage(playMsg("vidA", "A", 1));
await tick(); await tick();
out.error_sent = sent.filter((m) => m.event === "error");
out.iframes_after = document.querySelectorAll("iframe").length;
window.calls.length = 0;
panel.onMessage(playMsg("vidB", "B", 2));
await tick(); await tick();
out.iframes_now = document.querySelectorAll("iframe").length;
out.second_is_fresh = window.calls.filter((c) => c[0] === "new").length;
document.body.dataset.out = JSON.stringify(out);
"""
)


@pytest.fixture(scope="module")
def stuck() -> dict:
    return run_module_script(_STUCK_PLAYER_SCENARIO % {"panel": module_url("media-panel.js")})


@pytest.fixture(scope="module")
def mute_api() -> dict:
    return run_module_script(
        _MUTE_API_SCENARIO
        % {"panel": module_url("media-panel.js"), "mute": module_url("media-policy.js")}
    )


@pytest.fixture(scope="module")
def error_before_ready() -> dict:
    return run_module_script(_ERROR_BEFORE_READY_SCENARIO % {"panel": module_url("media-panel.js")})


def test_a_player_that_never_becomes_ready_is_reported_and_the_next_play_starts_fresh(stuck):
    assert stuck["iframe_while_waiting"] == 1
    assert stuck["error_sent"] == [
        {"type": "media_event", "event": "error", "video_id": "vidA", "code": "no_ready"}
    ]
    assert stuck["classes_after"] == []
    assert stuck["iframes_after"] == 0
    assert stuck["second_play_started"] is True
    assert stuck["iframes_now"] == 1


def test_an_api_script_that_loads_but_never_announces_itself_is_an_error_by_deadline(mute_api):
    assert mute_api["error_sent"] == [
        {"type": "media_event", "event": "error", "video_id": "vidA", "code": "api"}
    ]
    assert mute_api["iframes_after"] == 0
    assert mute_api["classes_after"] == []


def test_an_embed_that_errors_before_it_is_ready_is_reported_and_thrown_away(
    error_before_ready,
):
    assert error_before_ready["error_sent"] == [
        {"type": "media_event", "event": "error", "video_id": "vidA", "code": 150}
    ]
    assert error_before_ready["iframes_after"] == 0
    assert error_before_ready["iframes_now"] == 1
    assert error_before_ready["second_is_fresh"] == 1


# -- the player has a size, beside the face, with the real stylesheet -------

_SIZED_SCENARIO = (
    _STUB_YT
    + """
import { createMediaPanel } from "%(panel)s";
const panel = createMediaPanel(() => {});
"""
    + _COMMON
    + """
const rect = (sel) => { const el = document.querySelector(sel); if (!el) return null;
  const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width,
  height: r.height, bottom: r.bottom }; };
const display = (sel) => getComputedStyle(document.querySelector(sel)).display;
const results = [
  { index: 1, label: "One", title: "A", video_id: "vidA" },
  { index: 2, label: "Two", title: "B", video_id: "vidB" },
  { index: 3, label: "Three", title: "C", video_id: "vidC" },
];
panel.onMessage({ type: "state", state: "idle" });
panel.onMessage({ type: "media", action: "results", query: "q", results });
out.results_view = { results: display(".media-results"), player: display(".media-player") };
panel.onMessage(playMsg("vidA", "A", 1));
await tick(); await tick();
out.player_view = { results: display(".media-results"), player: display(".media-player") };
out.iframe = rect("iframe.media-player__iframe");
out.frame = rect(".media-player__frame");
out.face = rect("#face-container");
out.viewport = { width: window.innerWidth, height: window.innerHeight };
document.body.dataset.out = JSON.stringify(out);
"""
)


@pytest.fixture(scope="module")
def sized() -> dict:
    from tests.chromium_harness import stylesheet_link

    return run_module_script(
        _SIZED_SCENARIO % {"panel": module_url("media-panel.js")},
        body_html=stylesheet_link() + '<div id="face-container"></div>',
    )


def test_the_iframe_is_created_with_a_real_size_beside_the_face(sized):
    assert sized["viewport"]["width"] == 1920  # the kiosk's width; headless trims the height
    iframe = sized["iframe"]
    assert iframe is not None
    assert iframe["width"] > 600 and iframe["height"] > 300
    assert abs(iframe["width"] / iframe["height"] - 16 / 9) < 0.05
    assert iframe["left"] >= 960  # the right half; the face keeps the left
    assert iframe["bottom"] <= sized["viewport"]["height"] and iframe["top"] >= 0
    assert sized["face"]["width"] > 0 and sized["face"]["left"] == 0


def test_hidden_views_are_really_hidden_with_the_real_stylesheet(sized):
    # `hidden` used to lose to `.media-results { display: flex }`: the
    # results list and the player were both drawn at once.
    assert sized["results_view"] == {"results": "flex", "player": "none"}
    assert sized["player_view"] == {"results": "none", "player": "flex"}
