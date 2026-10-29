"""A card during a call, in the real renderer: cards.js, call-panel.js
and clock-panel.js together with the real style.css in headless
Chromium at the kiosk's 1920x1080 window.

Why it exists: before 2026-10-29 the card panel (right 50%, above the
phone panel) covered the End call button whenever both were up, and a
reminder or timer due during a call showed nothing at all. The owner's
call is a small silent card. What's pinned: every kind of card, while a
call is on, sits in a band at the top of the right side and never
overlaps End call, with every button whole inside the band, still 100px
tall, no text under 32px, and the face beside it; a long value stops at
two lines and never reaches the button; a tap on the compact
card answers it as before; once the call ends the card is full size
again; and a finished timer does not chime during a call (the other
person would hear it) but does once it is over.

The window is 1920x1080 but headless Chromium keeps some of it for its
own frame, so the page is shorter (993px here). Heights are asserted
against the page's own `innerHeight`; a shorter page is the harder
case, since the 100px and 32px floors do not shrink with it.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script, stylesheet_link

_SCENARIO = """
import { createCards } from "%(cards)s";
import { createCallPanel } from "%(call)s";
import { createClockPanel } from "%(clock)s";
const sent = [];
let chimes = 0;
const send = (m) => sent.push(m);
const cards = createCards(send);
const call = createCallPanel(send);
const clock = createClockPanel(send, { chime: () => { chimes += 1; } });
const out = { viewport: { width: innerWidth, height: innerHeight } };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const rect = (el) => {
  const r = el.getBoundingClientRect();
  return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width,
    height: r.height };
};
const px = (el, prop) => parseFloat(getComputedStyle(el)[prop]);
const measure = () => ({
  panel: rect(document.getElementById("card-panel")),
  end: rect(document.querySelector(".call-panel__end")),
  face: rect(document.getElementById("face-container")),
  name: rect(document.querySelector(".call-panel__name")),
  timer: rect(document.querySelector(".call-panel__timer")),
  buttons: [...document.querySelectorAll("#card-panel button")].map((b) => ({
    text: b.textContent, ...rect(b) })),
  text: [...document.querySelectorAll("#card-panel *")]
    .filter((el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()))
    .map((el) => ({ cls: el.className, size: px(el, "fontSize") })),
});

call.onMessage({ type: "call", call: { id: "c1", name: "Priya, your daughter",
  number: "+6591234567", status: "connected", elapsed_seconds: 65 } });

const KINDS = {
  confirm: { kind: "confirm", id: "k1", spoken: "…",
    title: "Done? take your blood pressure tablet; drink a full glass of water with it" },
  choice: { kind: "choice", id: "k2", title: "Which song would you like to hear now?",
    spoken: "…", options: [{ n: 1, label: "Tian Mi Mi by Teresa Teng" },
      { n: 2, label: "Yue Liang Dai Biao Wo De Xin" }, { n: 3, label: "Xiao Cheng Gu Shi" }] },
  readback: { kind: "readback", id: "k3", title: "Timer done", spoken: "…", value: "Tea" },
  readback_confirm: { kind: "readback", id: "k4", title: "Priya's number", spoken: "…",
    value: "+65 9123 4567", confirm: true },
  holding: { kind: "holding", id: "k5", title: "Keep holding to hang up", spoken: "…",
    progress: 0.4 },
};
out.during = {};
for (const [name, card] of Object.entries(KINDS)) {
  cards.onMessage({ type: "card", card });
  out.during[name] = measure();
}
// Far more than a band holds: it is cut off, End call is not covered.
cards.onMessage({ type: "card", card: { kind: "readback", id: "k6", title: "Timers done",
  spoken: "…", value: "Tea, eggs, rice, the oven, the washing, the bath, Priya's call back" } });
out.too_tall = measure();

cards.onMessage({ type: "card", card: KINDS.confirm });
document.querySelector(".card__option--yes").click();
out.tap = sent.slice();

cards.onMessage({ type: "card", card: KINDS.confirm });
call.onMessage({ type: "call", call: null });
out.after = measure();
cards.onMessage({ type: "card", card: null });

// A timer finishes during a call: no chime until the call is over.
call.onMessage({ type: "call", call: { id: "c2", name: "Priya", number: "+65",
  status: "connected", elapsed_seconds: 1 } });
const NOW = Date.now();
clock.onMessage({ type: "clock", visible: true, now_ms: NOW, stopwatch: null,
  timers: [{ id: "t1", label: "tea", seconds: 60, ends_at_ms: NOW - 1000, done: true }] });
await wait(600);
out.chimes_in_call = chimes;
call.onMessage({ type: "call", call: null });
await wait(600);
out.chimes_after = chimes;

document.body.dataset.out = JSON.stringify(out);
"""

KINDS = ("confirm", "choice", "readback", "readback_confirm", "holding")


@pytest.fixture(scope="module")
def results() -> dict:
    return run_module_script(
        _SCENARIO
        % {
            "cards": module_url("cards.js"),
            "call": module_url("call-panel.js"),
            "clock": module_url("clock-panel.js"),
        },
        body_html=stylesheet_link() + '<div id="face-container"></div>',
    )


def _overlap(a: dict, b: dict) -> bool:
    return (
        a["left"] < b["right"]
        and b["left"] < a["right"]
        and a["top"] < b["bottom"]
        and b["top"] < a["bottom"]
    )


def test_the_page_is_the_kiosk_width(results):
    assert results["viewport"]["width"] == 1920
    assert results["viewport"]["height"] <= 1080


@pytest.mark.parametrize("kind", KINDS)
def test_a_card_in_a_call_never_covers_end_call(results, kind):
    m, height = results["during"][kind], results["viewport"]["height"]
    assert m["end"]["height"] >= 112 and m["end"]["width"] >= 300
    assert m["end"]["bottom"] <= height
    assert not _overlap(m["panel"], m["end"])
    # Nor who she is talking to, or for how long.
    assert not _overlap(m["panel"], m["name"])
    assert not _overlap(m["panel"], m["timer"])


@pytest.mark.parametrize("kind", KINDS)
def test_the_compact_card_is_a_band_at_the_top_right_beside_the_face(results, kind):
    m, height = results["during"][kind], results["viewport"]["height"]
    assert m["panel"]["top"] == 0 and m["panel"]["right"] == 1920
    assert m["panel"]["left"] >= 1920 * 0.55
    assert m["panel"]["height"] <= height * 0.4
    assert m["face"]["left"] == 0 and 0 < m["face"]["right"] <= m["panel"]["left"]


@pytest.mark.parametrize("kind", KINDS)
def test_the_compact_card_keeps_the_floors_and_every_button_whole(results, kind):
    m = results["during"][kind]
    for button in m["buttons"]:
        assert button["height"] >= 100, button
        assert button["top"] >= m["panel"]["top"], button
        assert button["bottom"] <= m["panel"]["bottom"], button
        assert button["left"] >= m["panel"]["left"] and button["right"] <= 1920, button
    small = [t for t in m["text"] if t["size"] < 32]
    assert not small, small


def test_a_long_value_stops_at_two_lines_and_never_reaches_end_call(results):
    m = results["too_tall"]
    assert not _overlap(m["panel"], m["end"])
    assert m["panel"]["bottom"] < m["end"]["top"]
    # Its Okay is still whole: the value gave way, not the button.
    (okay,) = m["buttons"]
    assert okay["text"] == "Okay" and okay["height"] >= 100
    assert okay["bottom"] <= m["panel"]["bottom"]


def test_every_answerable_compact_card_still_has_its_way_out(results):
    texts = {kind: [b["text"] for b in results["during"][kind]["buttons"]] for kind in KINDS}
    assert texts["confirm"] == ["Yes", "No", "Never mind"]
    assert texts["choice"][-1] == "Never mind" and len(texts["choice"]) == 4
    assert texts["readback"] == ["Okay"]
    assert texts["readback_confirm"] == ["Yes", "No", "Never mind"]
    assert texts["holding"] == []


def test_a_tap_on_the_compact_card_answers_it(results):
    assert results["tap"] == [{"type": "card_answer", "id": "k1", "answer": {"yes": True}}]


def test_once_the_call_ends_the_card_is_full_size_again(results):
    m = results["after"]
    assert m["panel"]["left"] == 1920 / 2
    assert m["panel"]["height"] == results["viewport"]["height"]
    assert all(b["height"] >= 112 for b in m["buttons"])


def test_a_finished_timer_chimes_only_after_the_call(results):
    assert results["chimes_in_call"] == 0
    assert results["chimes_after"] >= 1
