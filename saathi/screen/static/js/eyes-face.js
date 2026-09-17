// Eyes, no mouth (SPEC.md, "The face"). Drives `roboeyes.js`'s
// reimplemented RoboEyes model and renders it warm: a soft dark ground
// (not OLED black) and eyes in warm gradients with a glow, easing
// continuously because the model already gives every value a target and
// a rate — this file only ever sets targets, never draws a jump cut.
//
// State -> model mapping (SPEC.md's four cues plus this checkpoint's
// explicit list): IDLE drifts and blinks; ATTENTIVE widens and looks
// toward her; LISTENING holds steady, slightly wider; THINKING looks away
// and up with lids lowered (mood TIRED is the closest available "lowered
// lids"); SPEAKING gets a small continuous bob and mood HAPPY — "narrowing
// at the corners for warmth" with no mouth to smile with. SLEEPING and
// HANDOFF aren't in that list; sleeping closes the eyes and stops idle
// drift, and handoff borrows `anim_confused()` once on entry — "the
// slower, smarter path" reads as a beat of confusion, not a mood.

import { Mood, RoboEyesModel } from "./roboeyes.js";

const GROUND_COLOR = "#171310";

const EYE_CONFIG = {
  leftEye: { width: 132, height: 132, borderRadius: 40 },
  rightEye: { width: 132, height: 132, borderRadius: 40 },
  spaceBetween: 48,
};

function roundedRectPath(ctx, x, y, width, height, radius) {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function drawEye(ctx, cx, cy, frame) {
  const left = cx + frame.x - frame.width / 2;
  const top = cy + frame.y - frame.height / 2;

  ctx.save();
  ctx.shadowColor = "rgba(255, 176, 89, 0.45)";
  ctx.shadowBlur = frame.width * 0.3;
  const gradient = ctx.createRadialGradient(
    cx + frame.x - frame.width * 0.15,
    cy + frame.y - frame.height * 0.25,
    frame.width * 0.05,
    cx + frame.x,
    cy + frame.y,
    frame.width * 0.75
  );
  gradient.addColorStop(0, "#fff6e2");
  gradient.addColorStop(0.55, "#ffce85");
  gradient.addColorStop(1, "#dd8a3f");
  ctx.fillStyle = gradient;
  roundedRectPath(ctx, left, top, frame.width, frame.height, frame.borderRadius);
  ctx.fill();
  ctx.restore();

  // Eyelids: separate shapes, drawn in the ground colour on top of the
  // eye, so they can be angled (tired/angry) or simply raised (happy).
  ctx.save();
  ctx.fillStyle = GROUND_COLOR;

  if (frame.tired > 0 || frame.angry > 0) {
    const lidHeight = frame.height * (frame.tired + frame.angry);
    // Tired droops the OUTER corner; angry droops the INNER corner.
    // `outerIsLeft` says which side of *this* eye is outer.
    const outerIsLeft = frame.outerIsLeft;
    const droopLeft = frame.tired > 0 ? outerIsLeft : !outerIsLeft;
    const apexX = droopLeft ? left : left + frame.width;
    ctx.beginPath();
    ctx.moveTo(left, top);
    ctx.lineTo(left + frame.width, top);
    ctx.lineTo(apexX, top + lidHeight);
    ctx.closePath();
    ctx.fill();
  }

  if (frame.happy > 0) {
    const offset = frame.height * frame.happy;
    roundedRectPath(
      ctx,
      left - 2,
      top + frame.height - offset,
      frame.width + 4,
      frame.height,
      frame.borderRadius
    );
    ctx.fill();
  }

  ctx.restore();
}

export default class EyesFace {
  async mount(container) {
    container.innerHTML = "";
    const canvas = document.createElement("canvas");
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.display = "block";
    container.appendChild(canvas);

    this._canvas = canvas;
    this._ctx = canvas.getContext("2d");
    this._model = new RoboEyesModel(EYE_CONFIG);
    this._model.setMood(Mood.DEFAULT);
    this._model.setAutoblinker(true, 1, 4);
    this._model.setIdleMode(true, 2, 3);
    this._lastState = null;
    this._lastFrameAt = performance.now();

    this._resize = () => this._resizeCanvas();
    window.addEventListener("resize", this._resize);
    this._resize();

    this._running = true;
    const loop = (now) => {
      if (!this._running) return;
      const dtMs = now - this._lastFrameAt;
      this._lastFrameAt = now;
      this._draw(dtMs);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  _resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this._canvas.parentElement.getBoundingClientRect();
    this._canvas.width = rect.width * dpr;
    this._canvas.height = rect.height * dpr;
    this._ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._widthCss = rect.width;
    this._heightCss = rect.height;
  }

  _draw(dtMs) {
    const ctx = this._ctx;
    const w = this._widthCss;
    const h = this._heightCss;

    ctx.fillStyle = GROUND_COLOR;
    ctx.fillRect(0, 0, w, h);

    const frame = this._model.tick(dtMs);
    const cx = w / 2;
    const cy = h / 2;
    drawEye(ctx, cx, cy, { ...frame.left, outerIsLeft: true });
    drawEye(ctx, cx, cy, { ...frame.right, outerIsLeft: false });
  }

  onState(state) {
    if (!this._model) return;
    const model = this._model;

    if (state !== "speaking") model.setSpeakingMotion(false);

    switch (state) {
      case "sleeping":
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setGazeTarget(0, 0);
        model.setSizeScale(1);
        model.close();
        break;
      case "idle":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setSizeScale(1);
        model.setGazeTarget(0, 0);
        model.setIdleMode(true, 2, 3);
        break;
      case "attentive":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setGazeTarget(0, 0);
        model.setSizeScale(1.18);
        break;
      case "listening":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setGazeTarget(0, 0);
        model.setSizeScale(1.08);
        break;
      case "thinking":
        model.open();
        model.setMood(Mood.TIRED);
        model.setIdleMode(false);
        model.setSizeScale(1);
        {
          const { x, y } = model.gazeRange;
          model.setGazeTarget(-x * 0.6, -y * 0.8);
        }
        break;
      case "speaking":
        model.open();
        model.setMood(Mood.HAPPY);
        model.setIdleMode(false);
        model.setSizeScale(1);
        model.setGazeTarget(0, 0);
        model.setSpeakingMotion(true);
        break;
      case "handoff":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setSizeScale(1);
        model.setGazeTarget(0, 0);
        if (this._lastState !== "handoff") model.anim_confused();
        break;
      default:
        break;
    }
    this._lastState = state;
  }

  unmount() {
    this._running = false;
    window.removeEventListener("resize", this._resize);
  }
}
