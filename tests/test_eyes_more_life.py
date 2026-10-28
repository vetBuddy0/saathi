"""The 2026-10-28 animations and the screen's tabs, stepped in a headless
Chromium.

What this pins: every new macro (wink, yawn, bounce, look-around) moves
the eyes and then settles exactly where it started, so none can leave
the face stuck mid-gesture. Sway only moves while it is on. The idle
repertoire yawns more at night and stays still under reduced motion.
A mounted face really looks toward a point on the right when asked
(`lookAt`). The tabs' calendar is whole Monday-first weeks, and a
refused call is said in her words, not the model's.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script

_SCRIPT = """
import { RoboEyesModel } from "%(model)s";
import { pickFlourish, default as EyesFace } from "%(face)s";
import { calendarCells, dayTitle, callRefusal } from "%(board)s";
const config = {
  leftEye: { width: 132, height: 132, borderRadius: 40 },
  rightEye: { width: 132, height: 132, borderRadius: 40 },
  spaceBetween: 48,
};
const run = (model, ms) => {
  let f;
  for (let t = 0; t < ms; t += 16) f = model.tick(16);
  return f;
};
const out = {};

for (const name of ["wink", "yawn", "bounce", "lookAround"]) {
  const m = new RoboEyesModel(config);
  m.setAutoblinker(false);
  const rest = run(m, 1500);
  m["anim_" + name](name === "wink" ? false : undefined);
  let minRightH = Infinity, minLeftH = Infinity, maxH = 0, minY = Infinity, minX = Infinity,
    maxX = -Infinity;
  for (let t = 0; t < 3200; t += 16) {
    const f = m.tick(16);
    minRightH = Math.min(minRightH, f.right.height);
    minLeftH = Math.min(minLeftH, f.left.height);
    maxH = Math.max(maxH, f.left.height);
    minY = Math.min(minY, f.left.y);
    minX = Math.min(minX, f.left.x);
    maxX = Math.max(maxX, f.left.x);
  }
  const settled = run(m, 2500);
  out[name] = {
    restH: rest.left.height, restX: rest.left.x, restY: rest.left.y,
    minRightH, minLeftH, maxH, minY, minX, maxX,
    settledLH: settled.left.height, settledRH: settled.right.height,
    settledX: settled.left.x, settledY: settled.left.y,
  };
}

{
  const m = new RoboEyesModel(config);
  m.setAutoblinker(false);
  const still = [];
  for (let t = 0; t < 2000; t += 16) still.push(m.tick(16).tilt);
  m.setSway(true);
  const swaying = [];
  for (let t = 0; t < 5000; t += 16) swaying.push(m.tick(16).tilt);
  m.setSway(false);
  const after = run(m, 100).tilt;
  out.sway = {
    still: Math.max(...still.map(Math.abs)),
    span: Math.max(...swaying) - Math.min(...swaying),
    after: Math.abs(after),
  };
}

{
  const count = (hour, reduced) => {
    const seen = {};
    for (let i = 0; i < 1000; i += 1) {
      const name = pickFlourish(i / 1000, hour, reduced);
      seen[name] = (seen[name] || 0) + 1;
    }
    return seen;
  };
  out.day = count(14, false);
  out.night = count(23, false);
  out.reduced = count(14, true);
}

out.cells = calendarCells("2026-10-28", 42).map((c) => [c.iso, c.past, c.inRange]);
out.titles = [
  dayTitle("2026-10-28", "2026-10-28"),
  dayTitle("2026-10-29", "2026-10-28"),
  dayTitle("2026-11-05", "2026-10-28"),
];
out.refusals = [
  callRefusal("Priya", { status: "busy" }),
  callRefusal("Priya", { status: "denied" }),
  callRefusal("Priya", { status: "unavailable", note: "Say so plainly." }),
];

{
  const box = document.getElementById("box");
  const face = new EyesFace();
  await face.mount(box);
  face.onState("idle");
  await new Promise((r) => setTimeout(r, 300));
  const before = face._model.left.xTarget - face._model.left.defaultX;
  const r = box.getBoundingClientRect();
  face.lookAt(r.right - 5, r.top + r.height / 2, 400);
  const during = face._model.left.xTarget - face._model.left.defaultX;
  await new Promise((r) => setTimeout(r, 700));
  out.glance = { before, during, after: face._model.left.xTarget - face._model.left.defaultX };
  face.onState("sleeping");
  face.lookAt(r.left + 5, r.top, 400);
  out.asleep_x = face._model.left.xTarget - face._model.left.defaultX;
  face.unmount();
}

document.body.dataset.out = JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def results() -> dict:
    return run_module_script(
        _SCRIPT
        % {
            "model": module_url("roboeyes.js"),
            "face": module_url("eyes-face.js"),
            "board": module_url("board.js"),
        },
        body_html='<div id="box" style="width:600px;height:400px"></div>',
    )


def _settles(r: dict) -> None:
    assert r["settledLH"] == pytest.approx(r["restH"], rel=0.01)
    assert r["settledRH"] == pytest.approx(r["restH"], rel=0.01)
    assert r["settledX"] == pytest.approx(r["restX"], abs=0.5)
    assert r["settledY"] == pytest.approx(r["restY"], abs=0.5)


def test_a_wink_closes_one_eye_only_and_reopens(results):
    wink = results["wink"]
    assert wink["minRightH"] < wink["restH"] * 0.3
    assert wink["minLeftH"] > wink["restH"] * 0.9
    _settles(wink)


def test_a_yawn_squeezes_then_stretches_tall_then_settles(results):
    yawn = results["yawn"]
    assert yawn["minLeftH"] < yawn["restH"] * 0.6
    assert yawn["maxH"] > yawn["restH"] * 1.1
    _settles(yawn)


def test_a_bounce_hops_up_and_lands(results):
    bounce = results["bounce"]
    assert bounce["minY"] < bounce["restY"] - 3
    _settles(bounce)


def test_look_around_goes_both_ways_and_comes_back(results):
    look = results["lookAround"]
    assert look["minX"] < look["restX"] - 20
    assert look["maxX"] > look["restX"] + 20
    _settles(look)


def test_sway_moves_only_while_on(results):
    sway = results["sway"]
    assert sway["still"] < 1e-6
    assert 0.05 < sway["span"] < 0.2  # a lean, not a spin
    assert sway["after"] < 1e-6


def test_the_idle_repertoire_yawns_more_at_night_and_keeps_still_when_asked(results):
    assert results["night"]["yawn"] > results["day"]["yawn"]
    assert set(results["day"]) >= {"squint", "tilt", "look-around", "wink", "bounce"}
    assert set(results["reduced"]) == {"squint", "tilt"}


def test_the_calendar_is_whole_monday_weeks_from_this_week(results):
    cells = results["cells"]
    assert len(cells) % 7 == 0
    assert cells[0][0] == "2026-10-26"  # the Monday of the week of the 28th
    assert cells[0][1] is True and cells[0][2] is False  # before today: past, not tappable
    today = [c for c in cells if c[0] == "2026-10-28"][0]
    assert today[1] is False and today[2] is True
    in_range = [c for c in cells if c[2]]
    assert len(in_range) == 42 and in_range[-1][0] == "2026-12-08"


def test_day_titles_are_words(results):
    assert results["titles"] == ["Today", "Tomorrow", "Thursday 5 November"]


def test_a_refused_call_is_said_in_her_words_never_the_models_note(results):
    busy, denied, other = results["refusals"]
    assert busy == "There's already a call going."
    assert denied == "Calling isn't allowed from here."
    assert other == "Couldn't call Priya just now."


def test_the_face_glances_toward_a_point_and_comes_back(results):
    glance = results["glance"]
    assert glance["during"] > glance["before"] + 10  # toward the right edge
    assert abs(glance["after"]) < abs(glance["during"])


def test_a_sleeping_face_does_not_glance(results):
    assert results["asleep_x"] == pytest.approx(0, abs=1e-6)
