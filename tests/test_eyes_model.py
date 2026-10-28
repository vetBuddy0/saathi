"""saathi/screen/static/js/roboeyes.js — the eyes' model, stepped by
hand in a headless Chromium (no canvas, no rAF: `tick()` is pure
numbers).

What this pins, from the 2026-10-07 expressiveness pass: a blink while
LISTENING reopens to the listening size, not idle size (a real bug the
bigger listening eyes made visible); the wake-word perk is a bounded
hop that settles; listening is never perfectly still (breathing); head
tilt stays small; and THINKING's drift never looks down at her.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script

_SCRIPT = """
import { Mood, RoboEyesModel } from "%(model)s";
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

{
  const m = new RoboEyesModel(config);
  m.setSizeScale(1.12);
  run(m, 1500);
  m.setBlinkSpeed(6);
  m.blink();
  let minHeight = Infinity;
  for (let t = 0; t < 3000; t += 16) minHeight = Math.min(minHeight, m.tick(16).left.height);
  out.blink_min = minHeight;
  out.after_blink = m.tick(16).left.height;
}

{
  const m = new RoboEyesModel(config);
  run(m, 500);
  const rest = m.tick(16).left;
  m.anim_perk();
  let minY = Infinity, maxW = 0;
  for (let t = 0; t < 650; t += 16) {
    const f = m.tick(16).left;
    minY = Math.min(minY, f.y);
    maxW = Math.max(maxW, f.width);
  }
  const settled = run(m, 500).left;
  out.perk = {
    restY: rest.y, restW: rest.width, minY, maxW, settledY: settled.y, settledW: settled.width,
  };
}

{
  const m = new RoboEyesModel(config);
  m.setBreathing(true, 0.025, 3.4);
  const widths = [];
  for (let t = 0; t < 3400; t += 16) widths.push(m.tick(16).left.width);
  out.breath_span = Math.max(...widths) - Math.min(...widths);
}

{
  const m = new RoboEyesModel(config);
  m.setTiltTarget(5);
  out.tilt = run(m, 3000).tilt;
}

{
  const m = new RoboEyesModel(config);
  m.setAttentiveNods(true, 0.1, 0.1);
  let maxTilt = 0;
  for (let t = 0; t < 5000; t += 16) maxTilt = Math.max(maxTilt, Math.abs(m.tick(16).tilt));
  out.nod_max_tilt = maxTilt;
}

{
  const m = new RoboEyesModel(config);
  m.setIdleMode(true, 0.05, 0.05, { x: [-0.8, 0.8], y: [-0.9, -0.55] });
  run(m, 100); // past the first drift; before it, the gaze is wherever the state left it
  let maxY = -Infinity;
  for (let t = 0; t < 4000; t += 16) { m.tick(16); maxY = Math.max(maxY, m.left.yTarget); }
  out.think_max_y = maxY;
}

{
  const m = new RoboEyesModel(config);
  m.setMood(Mood.DEFAULT);
  m.setWarmth(0.16);
  out.warm_happy = run(m, 2000).left.happy;
  m.setMood(Mood.HAPPY);
  out.happy_wins = run(m, 2000).left.happy;
}

document.body.dataset.out = JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def results() -> dict:
    return run_module_script(_SCRIPT % {"model": module_url("roboeyes.js")})


def test_a_blink_while_listening_reopens_to_listening_size(results):
    assert results["blink_min"] < 132 * 0.15  # it really closed
    assert results["after_blink"] == pytest.approx(132 * 1.12, rel=0.02)


def test_the_perk_hops_up_widens_and_settles(results):
    perk = results["perk"]
    assert perk["minY"] < perk["restY"] - 5  # up is negative y
    assert perk["maxW"] > perk["restW"] * 1.05
    assert perk["settledY"] == pytest.approx(perk["restY"], abs=0.5)
    assert perk["settledW"] == pytest.approx(perk["restW"], rel=0.01)


def test_breathing_moves_the_eyes_a_little(results):
    assert 132 * 0.03 < results["breath_span"] < 132 * 0.08


def test_tilt_stays_small(results):
    assert results["tilt"] <= 0.2 + 1e-9
    assert 0.02 < results["nod_max_tilt"] <= 0.1


def test_thinking_drift_only_wanders_along_the_top(results):
    assert results["think_max_y"] < 0


def test_warmth_layers_on_default_and_happy_still_wins(results):
    assert results["warm_happy"] == pytest.approx(0.16, abs=0.01)
    assert results["happy_wins"] == pytest.approx(0.55, abs=0.01)
