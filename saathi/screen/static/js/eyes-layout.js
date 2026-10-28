// How big the eyes are drawn, derived from the box they live in — never
// from the window, never a fixed pixel size. Pure numbers, no canvas, so
// tests/test_eyes_extras.py can run it headless.
//
// Why this exists: the eyes used to be a fixed 132 px pair in the middle
// of a 1080p screen (small, "a screensaver"), and when a video went
// fullscreen and the face moved into a corner, the canvas was stretched
// to the corner's shape while the eyes stayed 132 px — cropped and
// squashed. The product owner (2026-10-07) wanted them "quite big",
// filling most of the screen, and properly fitted in any smaller box
// (the fullscreen-video corner, the left part beside a phone call).
//
// The model (roboeyes.js) keeps working in its own fixed units — its
// tests and its easing are all written against a 132-unit eye — and the
// renderer multiplies by `fitScale()`. One uniform scale for both axes,
// so proportions are always kept. The option that lost was resizing the
// model's eyes on every container change: every target, gaze range and
// blink threshold would have had to be recomputed mid-animation.

// The model's eye pair, in model units (eyes-face.js's EYE_CONFIG).
export const PAIR = Object.freeze({ width: 132 * 2 + 48, height: 132 });

// How much of the box the resting pair may take. Height is the tighter
// one on a landscape screen: listening/surprised eyes grow up to ~1.3x
// and the gaze needs a little room to travel. Smaller (2026-10-08,
// owner): "quite big" (0.66 x 0.47) read as too much; about two
// thirds of that now.
const FILL_WIDTH = 0.42;
const FILL_HEIGHT = 0.3;

export function fitScale(boxWidth, boxHeight, pair = PAIR) {
  if (!(boxWidth > 0) || !(boxHeight > 0)) return 0;
  return Math.min((boxWidth * FILL_WIDTH) / pair.width, (boxHeight * FILL_HEIGHT) / pair.height);
}

// The gap kept between the eyes and the edge of the box, in CSS px.
export function edgeMargin(boxWidth, boxHeight) {
  return Math.min(boxWidth, boxHeight) * 0.04;
}

// Gaze moves the whole pair; at this size the model's gaze range would
// carry the eyes off the box. Returns the correction (model units) that
// keeps the pair inside: `centerX/Y` is the pair's centre offset and
// `halfW/halfH` its half extent, both in model units, as drawn this
// frame. A pair bigger than the box is centred, never pushed further.
export function clampPairOffset(centerX, centerY, halfW, halfH, scale, boxWidth, boxHeight) {
  if (!(scale > 0)) return { dx: 0, dy: 0 };
  const margin = edgeMargin(boxWidth, boxHeight);
  const roomX = Math.max(0, (boxWidth / 2 - margin) / scale - halfW);
  const roomY = Math.max(0, (boxHeight / 2 - margin) / scale - halfH);
  const x = Math.max(-roomX, Math.min(roomX, centerX));
  const y = Math.max(-roomY, Math.min(roomY, centerY));
  return { dx: x - centerX, dy: y - centerY };
}

// Where to look so the eyes follow something on screen: the object's
// position relative to the box centre, as a fraction of the half box,
// times the model's gaze range. Clamped by the model anyway.
export function gazeToward(objectX, objectY, boxWidth, boxHeight, range) {
  const fx = (objectX - boxWidth / 2) / (boxWidth / 2);
  const fy = (objectY - boxHeight / 2) / (boxHeight / 2);
  const clamp = (v) => Math.max(-1, Math.min(1, v));
  return { x: clamp(fx) * range.x, y: clamp(fy) * range.y };
}

// Ambient visitors only come when the face has the screen: not beside a
// video, not in the fullscreen-video corner, not beside a phone call.
// Derived from the box, so the face never has to be told about media or
// calls (it is driven by core.py's state alone — SPEC.md).
export function hasTheStage(boxWidth, windowWidth) {
  return windowWidth > 0 && boxWidth >= windowWidth * 0.7;
}
