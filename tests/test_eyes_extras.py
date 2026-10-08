"""The bigger, playful eyes (2026-10-07): eyes-layout.js, eyes-ambient.js,
the model's blush, and EyesFace's onEmotion -- in a headless Chromium,
like tests/test_eyes_model.py (no node on the device or the dev box).

What this pins: the eyes fill most of a full screen and still fit,
whole and in proportion, in the fullscreen-video corner and beside a
call; gaze can never carry them off the box; a visitor only comes while
idle on the full screen and the eyes' gaze follows it; z's rise; a
blush eases in; an emotion is layered on and lapses back, and is
ignored while asleep.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script

_SCRIPT = """
import { PAIR, fitScale, clampPairOffset, gazeToward, hasTheStage } from "%(layout)s";
import { AmbientDirector, Floaters } from "%(ambient)s";
import { RoboEyesModel } from "%(model)s";
import EyesFace from "%(face)s";
const out = {};

const fit = (w, h) => {
  const s = fitScale(w, h);
  return { s, pairW: PAIR.width * s, pairH: PAIR.height * s, w, h };
};
out.full = fit(1920, 1080);
out.corner = fit(422, 238);       // 22vw x 22vh at 1080p
out.call = fit(1152, 1080);       // left 60%% beside the phone panel
out.empty = fitScale(0, 500);

{
  // Gaze at the bottom of the model's range, eyes at full size.
  const s = fitScale(1920, 1080);
  const { dy } = clampPairOffset(0, 145, 156, 66, s, 1920, 1080);
  out.clamped_bottom = (145 + dy + 66) * s;   // bottom edge, px from centre
  const inside = clampPairOffset(5, -5, 156, 66, s, 1920, 1080);
  out.inside = inside;
}

out.gaze_left_top = gazeToward(0, 0, 1000, 500, { x: 100, y: 50 });
out.gaze_far = gazeToward(5000, 250, 1000, 500, { x: 100, y: 50 });
out.stage = [hasTheStage(1920, 1920), hasTheStage(960, 1920), hasTheStage(422, 1920)];

{
  let i = 0;
  const seq = [0.1, 0.9, 0.5, 0.3, 0.7];
  const random = () => seq[i++ %% seq.length];
  const director = new AmbientDirector({ random });
  let seenInactive = 0;
  for (let t = 0; t < 20000; t += 16) {
    if (director.tick(16, { active: false, width: 1920, height: 1080 })) seenInactive += 1;
  }
  const xs = [];
  let kind = null;
  for (let t = 0; t < 40000; t += 16) {
    const v = director.tick(16, { active: true, width: 1920, height: 1080 });
    if (v) { xs.push(v.x); kind = v.kind; }
  }
  const midway = (() => {
    const d = new AmbientDirector({ random });
    let v = null;
    const on = { active: true, width: 1920, height: 1080 };
    for (let t = 0; t < 20000 && !v; t += 16) v = d.tick(16, on);
    const dropped = d.tick(16, { active: false, width: 1920, height: 1080 });
    return { had: Boolean(v), dropped, current: d.current };
  })();
  out.ambient = {
    seenInactive, frames: xs.length, kind,
    minX: Math.min(...xs), maxX: Math.max(...xs), midway,
  };
}

{
  const f = new Floaters();
  let items = [];
  const at = { originX: 100, originY: 500, size: 50 };
  for (let t = 0; t < 3000; t += 16) items = f.tick(16, { active: true, ...at });
  const ys = items.map((it) => it.y);
  let after = items;
  for (let t = 0; t < 4000; t += 16) after = f.tick(16, { active: false, ...at });
  out.floaters = {
    count: items.length, allAbove: ys.every((y) => y <= 500),
    glyph: items[0].glyph, after: after.length,
  };
}

{
  const m = new RoboEyesModel({
    leftEye: { width: 132, height: 132, borderRadius: 40 },
    rightEye: { width: 132, height: 132, borderRadius: 40 },
    spaceBetween: 48,
  });
  m.setBlush(1);
  const early = m.tick(16).blush;
  let f;
  for (let t = 0; t < 2000; t += 16) f = m.tick(16);
  m.setBlush(5);
  out.blush = { early, settled: f.blush, clampedTarget: m.blushTarget };
}

{
  const box = document.getElementById("box");
  const face = new EyesFace();
  await face.mount(box);
  face.onState("idle");
  face.onEmotion("blush", 0.5);
  out.emotion_on = { emotion: face._emotion, blush: face._model.blushTarget };
  face.onEmotion("smug", 3);
  out.emotion_unknown = { emotion: face._emotion, blush: face._model.blushTarget };
  face.onEmotion("surprised", 0.5);
  await new Promise((r) => setTimeout(r, 900));
  out.emotion_lapsed = face._emotion;
  face.onState("sleeping");
  face.onEmotion("happy", 3);
  out.emotion_asleep = face._emotion;
  face.onState("idle");
  face.onEmotion("sad", 3);
  face.onState("sleeping");
  out.emotion_cleared_by_sleep = face._emotion;

  // The canvas follows its container, not the window.
  box.style.width = "400px";
  box.style.height = "225px";
  await new Promise((r) => setTimeout(r, 300));
  const dpr = window.devicePixelRatio || 1;
  out.canvas = { w: face._canvas.width / dpr, h: face._canvas.height / dpr };
  face.unmount();
}

document.body.dataset.out = JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def results() -> dict:
    return run_module_script(
        _SCRIPT
        % {
            "layout": module_url("eyes-layout.js"),
            "ambient": module_url("eyes-ambient.js"),
            "model": module_url("roboeyes.js"),
            "face": module_url("eyes-face.js"),
        },
        body_html='<div id="box" style="width:1920px;height:1080px"></div>',
    )


def test_the_eyes_take_a_calm_share_of_a_full_screen(results):
    # 2026-10-08, owner: smaller than the old "most of the screen"
    # (>= 60% wide) -- big enough to read across a room, not dominating.
    full = results["full"]
    assert full["pairW"] >= full["w"] * 0.25 or full["pairH"] >= full["h"] * 0.25
    assert full["pairW"] <= full["w"] * 0.45 and full["pairH"] <= full["h"] * 0.32


@pytest.mark.parametrize("layout", ["corner", "call"])
def test_smaller_boxes_scale_the_whole_pair_down_and_it_still_fits(layout, results):
    box = results[layout]
    assert 0 < box["s"] < results["full"]["s"]
    # Room for the biggest look (surprised, ~1.3x) without cropping.
    assert box["pairW"] * 1.1 <= box["w"]
    assert box["pairH"] * 1.3 <= box["h"]


def test_an_empty_box_draws_nothing_rather_than_dividing_by_zero(results):
    assert results["empty"] == 0


def test_gaze_never_carries_the_eyes_off_the_box(results):
    assert results["clamped_bottom"] < 540
    assert results["inside"] == {"dx": 0, "dy": 0}


def test_gaze_toward_an_object_points_at_it_within_range(results):
    assert results["gaze_left_top"] == {"x": -100, "y": -50}
    assert results["gaze_far"] == {"x": 100, "y": 0}


def test_visitors_only_come_when_the_face_has_the_screen(results):
    assert results["stage"] == [True, False, False]


def test_a_visitor_comes_only_while_active_and_crosses_the_screen(results):
    ambient = results["ambient"]
    assert ambient["seenInactive"] == 0
    assert ambient["frames"] > 0 and ambient["kind"] in ("ball", "leaf", "star")
    midway = ambient["midway"]
    assert midway["had"] and midway["dropped"] is None and midway["current"] is None


def test_zs_rise_from_their_origin_and_stop_when_asleep_ends(results):
    floaters = results["floaters"]
    assert floaters["count"] >= 2 and floaters["allAbove"] and floaters["glyph"] == "z"
    assert floaters["after"] == 0


def test_a_blush_eases_in_and_is_clamped(results):
    blush = results["blush"]
    assert 0 < blush["early"] < 0.2
    assert blush["settled"] == pytest.approx(1, abs=0.01)
    assert blush["clampedTarget"] == 1


def test_an_emotion_layers_on_then_lapses_and_sleep_ignores_it(results):
    assert results["emotion_on"] == {"emotion": "blush", "blush": 1}
    assert results["emotion_unknown"] == {"emotion": None, "blush": 0}
    assert results["emotion_lapsed"] is None
    assert results["emotion_asleep"] is None
    assert results["emotion_cleared_by_sleep"] is None


def test_the_canvas_follows_its_container_not_the_window(results):
    assert results["canvas"] == {"w": 400, "h": 225}
