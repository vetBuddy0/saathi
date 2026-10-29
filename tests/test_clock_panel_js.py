"""saathi/screen/static/js/clock-panel.js with the real style.css in a
1920x1080 headless Chromium.

What's pinned: a `clock` message draws the time and date, each timer
counting down from the server's clock (not the page's), and the
stopwatch counting up, on the right with the face beside it; a
finished timer reads "Time's up", chimes, and its Stop sends one
`dismiss`; the close button sends `hide`; a card or a call takes the
right of the screen instead; `visible: false` gives the face its screen
back; a malformed message is dropped.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script, stylesheet_link

_SCENARIO = """
import { createClockPanel, timerName, remaining } from "%(panel)s";
const sent = [];
let chimes = 0;
// The page's clock is 7 s behind the server's; counts follow the server.
let pageNow = 1000000;
const SERVER = pageNow + 7000;
const panel = createClockPanel((m) => sent.push(m),
  { now: () => pageNow, chime: () => { chimes += 1; } });
const out = {};
const root = document.getElementById("clock-panel");
const face = document.getElementById("face-container");
const text = (sel) => root.querySelector(sel).textContent;
const counts = () => [...root.querySelectorAll(".clock-panel__count")]
  .filter((el) => el.offsetParent !== null).map((el) => el.textContent);
const rect = (el) => {
  const r = el.getBoundingClientRect();
  return { left: r.left, right: r.right, width: r.width, height: r.height,
    shown: getComputedStyle(el).display !== "none" };
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const message = (over) => ({ type: "clock", visible: true, now_ms: SERVER, timers: [],
  stopwatch: null, ...over });

out.names = [timerName({ label: "tea", seconds: 60 }), timerName({ label: "", seconds: 300 }),
  timerName({ label: "", seconds: 3661 }), timerName({ label: "", seconds: 0 })];
out.remaining = [remaining(60000, 0), remaining(60000, 59800), remaining(0, 5000)];
out.closed = { hidden: root.hidden, open: document.body.classList.contains("clock--open") };

panel.onMessage(message({
  timers: [{ id: "t1", label: "tea", seconds: 300, ends_at_ms: SERVER + 300000, done: false }],
  stopwatch: { running: true, elapsed_ms: 5000, since_ms: SERVER - 10000 },
}));
out.open = {
  time: text(".clock-panel__time"), date: text(".clock-panel__date"),
  labels: [...root.querySelectorAll(".clock-panel__label")].map((el) => el.textContent),
  counts: counts(), panel: rect(root), face: rect(face),
  close: rect(root.querySelector(".clock-panel__close")),
};
pageNow += 61000;
await wait(400);
out.later = counts();

pageNow = 1000000 + 300000;
panel.onMessage(message({
  now_ms: SERVER + 300000,
  timers: [{ id: "t1", label: "tea", seconds: 300, ends_at_ms: SERVER + 300000, done: true }],
}));
await wait(300);
const stop = root.querySelector(".clock-panel__stop");
out.done = { counts: counts(), stop: rect(stop), stopText: stop.textContent, chimes };
pageNow += 16000;
await wait(400);
out.chimes_again = chimes;
stop.click();
stop.click();
pageNow += 16000;
await wait(400);
out.after_stop = { chimes, stop: rect(stop).shown };
root.querySelector(".clock-panel__close").click();
out.sent = sent.slice();

document.body.classList.add("card--open");
out.with_card = { clock: rect(root).shown, face: rect(face) };
document.body.classList.remove("card--open");

panel.onMessage({ type: "clock", visible: "yes", timers: [] });
panel.onMessage({ type: "clock", visible: false });
out.after_malformed = rect(root).shown;

panel.onMessage(message({ visible: false }));
out.hidden = { hidden: root.hidden, open: document.body.classList.contains("clock--open"),
  face: rect(face) };

document.body.dataset.out = JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def results() -> dict:
    return run_module_script(
        _SCENARIO % {"panel": module_url("clock-panel.js")},
        body_html=stylesheet_link() + '<div id="face-container"></div>',
    )


def test_timer_names_and_what_is_left(results):
    assert results["names"] == ["Tea", "5 minutes", "1 hour 1 minute 1 second", "Timer"]
    assert results["remaining"] == ["1:00", "0:01", "0:00"]


def test_nothing_is_drawn_before_the_clock_is_asked_for(results):
    assert results["closed"] == {"hidden": True, "open": False}


def test_the_clock_shows_the_time_a_timer_and_the_stopwatch_on_the_right(results):
    shown = results["open"]
    assert shown["time"] and shown["date"]
    assert shown["labels"] == ["Tea", "Stopwatch"]
    # Counted from the server's clock: 5:00 left, 15 s on the stopwatch.
    assert shown["counts"] == ["5:00", "0:15"]
    assert shown["panel"]["left"] >= 1920 * 0.55 and shown["panel"]["right"] == 1920
    assert shown["face"]["left"] == 0 and 0 < shown["face"]["right"] <= shown["panel"]["left"]
    assert shown["close"]["height"] >= 100 and shown["close"]["width"] >= 100


def test_both_count_on_the_page(results):
    assert results["later"] == ["3:59", "1:16"]


def test_a_finished_timer_says_times_up_chimes_and_stops_on_one_tap(results):
    done = results["done"]
    assert done["counts"] == ["Time's up"]
    assert done["stopText"] == "Stop" and done["stop"]["shown"]
    assert done["stop"]["height"] >= 100
    assert done["chimes"] == 1
    assert results["chimes_again"] == 2  # rings again, like a kitchen timer
    assert results["after_stop"] == {"chimes": 2, "stop": False}


def test_the_taps_send_dismiss_once_then_hide(results):
    assert results["sent"] == [
        {"type": "clock_event", "action": "dismiss", "id": "t1"},
        {"type": "clock_event", "action": "hide"},
    ]


def test_a_card_takes_the_right_of_the_screen_instead(results):
    with_card = results["with_card"]
    assert with_card["clock"] is False
    assert with_card["face"]["right"] == 1920 * 0.5


def test_a_malformed_message_is_dropped(results):
    assert results["after_malformed"] is True


def test_hidden_gives_the_face_its_screen_back(results):
    hidden = results["hidden"]
    assert hidden["hidden"] is True and hidden["open"] is False
    assert hidden["face"]["width"] == 1920
