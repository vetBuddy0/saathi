"""saathi/screen/static/js/cards.js in the real renderer: the four
cards drawn with the real style.css in a 1920x1080 headless Chromium,
sizes read back from getComputedStyle / getBoundingClientRect.

What's pinned: every tap target at least 100px tall; no text under
32px; a large dismiss on every answerable card; a tap sends the one
`card_answer` shape; a second card replaces the first (never two in
the DOM); `card: null` clears; a holding card advancing keeps its node;
the face's container is beside the card, never hidden; a malformed
card is dropped.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script, stylesheet_link

_SCENARIO = """
import { createCards } from "%(cards)s";
const sent = [];
const cards = createCards((m) => sent.push(m));
const out = {};
const px = (el, prop) => parseFloat(getComputedStyle(el)[prop]);
const textSizes = () =>
  Array.from(document.querySelectorAll("#card-panel *"))
    .filter((el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim()))
    .map((el) => ({ cls: el.className, size: px(el, "fontSize") }));
const targets = () =>
  Array.from(document.querySelectorAll("#card-panel button")).map((el) => ({
    cls: el.className, text: el.textContent, height: el.getBoundingClientRect().height,
    width: el.getBoundingClientRect().width,
  }));
const faceRect = () => {
  const r = document.getElementById("face-container").getBoundingClientRect();
  return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, visible: r.width > 0 };
};

const choice = {
  kind: "choice", id: "c1", title: "Which Priya?", spoken: "…",
  options: [
    { n: 1, label: "Priya, your daughter" },
    { n: 2, label: "Priya from church" },
    { n: 3, label: "Priya Menon" },
  ],
};
cards.onMessage({ type: "card", card: choice });
out.choice = {
  cards_in_dom: document.querySelectorAll(".card").length,
  body_class: Array.from(document.body.classList),
  text: textSizes(),
  targets: targets(),
  face: faceRect(),
  numbers: Array.from(document.querySelectorAll(".card__number")).map((e) => e.textContent),
};
document.querySelectorAll(".card__option")[1].click();
out.choice.sent_on_tap = sent.slice();
out.choice.after_tap_cards = document.querySelectorAll(".card").length;

const show = (card) => cards.onMessage({ type: "card", card });
sent.length = 0;
show(choice);
show({ kind: "confirm", id: "k1", title: "Call Priya?", spoken: "…" });
out.confirm = {
  cards_in_dom: document.querySelectorAll(".card").length,
  id: document.querySelector(".card").dataset.cardId,
  text: textSizes(),
  targets: targets(),
};
document.querySelector(".card__option--no").click();
out.confirm.sent = sent.slice();

sent.length = 0;
show({ kind: "readback", id: "r1", title: "Priya's number", spoken: "…", value: "041 234 5678" });
out.readback = {
  text: textSizes(),
  targets: targets(),
  value: document.querySelector(".card__value").textContent,
};
document.querySelector(".card__dismiss").click();
out.readback.sent = sent.slice();

sent.length = 0;
const hold = (progress) =>
  ({ kind: "holding", id: "h1", title: "Keep holding to hang up", spoken: "…", progress });
show(hold(0.25));
const holdNode = document.querySelector(".card");
const fillAt = () => document.querySelector(".card__progress-fill").style.width;
out.holding = { text: textSizes(), targets: targets(), fill_start: fillAt() };
show(hold(0.75));
out.holding.fill_later = fillAt();
out.holding.same_node = document.querySelector(".card") === holdNode;
out.holding.bar_height = document.querySelector(".card__progress").getBoundingClientRect().height;

cards.onMessage({ type: "card", card: { kind: "bogus", id: "x", title: "?", spoken: "?" } });
out.bogus_kept_holding = document.querySelector(".card").dataset.cardId;
cards.onMessage({ type: "card", card: { kind: "choice", id: "x2", title: "?", spoken: "?" } });
out.bogus_choice_kept_holding = document.querySelector(".card").dataset.cardId;

cards.onMessage({ type: "card", card: null });
out.cleared = {
  cards_in_dom: document.querySelectorAll(".card").length,
  hidden: document.getElementById("card-panel").hidden,
  body_class: Array.from(document.body.classList),
  face: faceRect(),
};
out.nothing_sent_on_clear = sent.length;
document.body.dataset.out = JSON.stringify(out);
"""

_BODY = '<div id="face-container"></div>'


@pytest.fixture(scope="module")
def panel() -> dict:
    return run_module_script(
        _SCENARIO % {"cards": module_url("cards.js")}, body_html=stylesheet_link() + _BODY
    )


def _assert_floors(section: dict) -> None:
    small = [t for t in section["text"] if t["size"] < 32]
    assert not small, f"text under 32px: {small}"
    short = [t for t in section["targets"] if t["height"] < 100]
    assert not short, f"tap targets under 100px: {short}"


def test_choice_draws_three_numbered_targets_beside_the_face(panel):
    c = panel["choice"]
    assert c["cards_in_dom"] == 1
    assert c["body_class"] == ["card--open"]
    assert c["numbers"] == ["1", "2", "3"]
    _assert_floors(c)
    labels = [t["text"] for t in c["targets"]]
    assert labels == ["1Priya, your daughter", "2Priya from church", "3Priya Menon", "Never mind"]
    dismiss = c["targets"][-1]
    assert dismiss["height"] >= 100 and dismiss["width"] >= 600  # large and obvious, not a ✕
    face = c["face"]
    assert face["visible"] and face["left"] == 0 and face["right"] == 960


def test_a_tap_on_the_second_option_sends_exactly_one_card_answer_and_clears_nothing_locally(
    panel,
):
    c = panel["choice"]
    assert c["sent_on_tap"] == [{"type": "card_answer", "id": "c1", "answer": {"choice": 2}}]
    # The card stays until the server clears it (card: null) -- the
    # browser doesn't decide that its own tap succeeded.
    assert c["after_tap_cards"] == 1


def test_a_second_card_replaces_the_first_never_stacks(panel):
    assert panel["confirm"]["cards_in_dom"] == 1
    assert panel["confirm"]["id"] == "k1"


def test_confirm_has_yes_no_and_a_way_out_all_large(panel):
    c = panel["confirm"]
    _assert_floors(c)
    assert [t["text"] for t in c["targets"]] == ["Yes", "No", "Never mind"]
    assert c["sent"] == [{"type": "card_answer", "id": "k1", "answer": {"yes": False}}]


def test_readback_shows_the_grouped_value_very_large_with_an_okay(panel):
    r = panel["readback"]
    _assert_floors(r)
    assert r["value"] == "041 234 5678"
    value_size = next(t["size"] for t in r["text"] if t["cls"] == "card__value")
    assert value_size >= 100
    assert [t["text"] for t in r["targets"]] == ["Okay"]
    assert r["sent"] == [{"type": "card_answer", "id": "r1", "answer": {"dismiss": True}}]


def test_holding_advances_in_place_with_no_button(panel):
    h = panel["holding"]
    _assert_floors(h)
    assert h["targets"] == []  # letting go is the way out
    assert h["fill_start"] == "25%"
    assert h["fill_later"] == "75%"
    assert h["same_node"] is True
    assert h["bar_height"] >= 48


def test_a_malformed_card_is_dropped_and_the_current_one_stays(panel):
    assert panel["bogus_kept_holding"] == "h1"
    assert panel["bogus_choice_kept_holding"] == "h1"


def test_null_clears_the_card_and_gives_the_face_the_whole_screen_back(panel):
    c = panel["cleared"]
    assert c["cards_in_dom"] == 0
    assert c["hidden"] is True
    assert c["body_class"] == []
    assert c["face"]["right"] == 1920
    assert panel["nothing_sent_on_clear"] == 0
