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
//
// More expressive (2026-10-07): the first demo's listening eyes read as
// a held pose. Each state now has a *living* version of its look, built
// from roboeyes.js's expressiveness layer (see its docstring):
//   IDLE       blinks irregularly, drifts, and every several seconds a
//              small flourish: a curious tilt or a happy squint;
//   ATTENTIVE  the wake word: a perk-up hop, eyes widen and brighten,
//              then hold wide and round — "oh, you called me?";
//   LISTENING  bigger and rounder than idle, gaze on her, slow breathing,
//              small nods and tilts, slow calm blinks, a warm low lid
//              and a bright catchlight — a pet paying close attention;
//   THINKING   lids lowered, gaze wandering along the top, away from her;
//   SPEAKING   warm squint with a lively bob and a slight syllable squash;
//   SLEEPING   closed, dim, breathing slowly.
// The catchlight is a soft glint, not a pupil: a pupil would have to
// look *somewhere*, and gaze already does that job with the whole eye.
//
// Bigger and playful (2026-10-07, the product owner's explicit ask, past
// SPEC.md's "eyebrows read as children's illustration" caution — see
// DECISIONS.md for the option that lost):
//   - size comes from the box the face is in (eyes-layout.js): most of
//     the screen when the face has it, scaled down whole — never cropped
//     or squashed — in the fullscreen-video corner or beside a call. A
//     ResizeObserver, not window resize, because those layouts change
//     the container without the window changing;
//   - while idle on the full screen, now and then a ball, a leaf or a
//     star passes and the eyes follow it (eyes-ambient.js);
//   - asleep: closed, softly curved eyes and rising z's;
//   - `onEmotion(name, seconds)`: blush (pink cheeks, a shy glance away),
//     happy, love (hearts), sad, surprised, curious, layered on top of
//     the state's look and lapsing back to it. An optional Face method —
//     faces without it simply don't show emotions (face.js).
//
// Calmer (2026-10-08, owner: "calm down on the animations"): the same
// looks at about half the energy -- idle drift every 4-9 s (was 2-5),
// flourishes every 15-30 s (was 5-11), a visitor every 1-2 min (was
// 14-30 s), smaller attentive/listening growth, slower and fewer nods,
// and a gentler speaking bob, perk and syllable squash (roboeyes.js).
// Lost: removing the flourishes and visitors outright -- they are what
// makes it read as alive rather than a screensaver; rarer keeps that.
// Still no mouth, no eyebrows, no words.

import { Mood, RoboEyesModel } from "./roboeyes.js";
import { clampPairOffset, fitScale, gazeToward, hasTheStage } from "./eyes-layout.js";
import { AmbientDirector, Floaters, drawFloater, drawVisitor } from "./eyes-ambient.js";

export const EMOTION_NAMES = Object.freeze([
  "happy",
  "blush",
  "love",
  "sad",
  "surprised",
  "curious",
]);

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

const GROUND_COLOR = "#171310";

// The glow is blurred on its own canvas, this many times smaller than the
// face, which sits *behind* the face canvas and is stretched to size by
// CSS -- so the browser's compositor does the upscaling, not a drawImage.
// A blur has no detail to lose, and its cost grows with pixels x radius.
// Measured (2026-10-08, headless Firefox, 3840x2230 at dpr 2, frame time
// median): full-size shadowBlur 101-132 ms -- the stutter the owner saw
// when the eyes moved; quarter-size blur drawn back up 35-38 ms; plus a
// rectangular redraw box 26-30 ms; plus this layering and drawing the
// eyes straight onto the face canvas (no offscreen copy) -- see
// DECISIONS.md for the last number. Lost: full-resolution shadowBlur,
// and a CSS blur filter (it would blur the eyes' own edges too).
const GLOW_DOWNSCALE = 4;

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

// Draws one eye onto the *offscreen* layer: body, catchlight, then the
// lids erased out of it. No glow here — `_draw` adds the glow when it
// composites the layer, so the halo follows the shape that's actually
// left after the lids, not the eye's full outline (2026-10-07: painting
// lids in the ground colour on top left the glow tracing the hidden
// part, and a smiling eye read as a dark bowl).
function drawEye(ctx, cx, cy, frame, look) {
  const left = cx + frame.x - frame.width / 2;
  const top = cy + frame.y - frame.height / 2;

  // Asleep: a closed eye is a soft downward curve, not a thin bar — the
  // bar read as "switched off", the curve as "sleeping peacefully".
  if (look.asleep && frame.height < frame.width * 0.2) {
    ctx.save();
    ctx.strokeStyle = "#f2b46a";
    ctx.lineCap = "round";
    ctx.lineWidth = Math.max(2, frame.width * 0.075);
    ctx.beginPath();
    const y = cy + frame.y - frame.width * 0.06;
    ctx.moveTo(left + frame.width * 0.12, y);
    ctx.quadraticCurveTo(cx + frame.x, y + frame.width * 0.24, left + frame.width * 0.88, y);
    ctx.stroke();
    ctx.restore();
    return;
  }

  ctx.save();
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

  // Catchlight: a soft glint up and to the left, clipped to the eye so
  // a blink takes it with it. Sized from the eye, never a fixed pixel.
  if (look.sparkle > 0.01 && frame.height > frame.width * 0.2) {
    ctx.save();
    roundedRectPath(ctx, left, top, frame.width, frame.height, frame.borderRadius);
    ctx.clip();
    const r = Math.min(frame.width, frame.height) * 0.16;
    const gx = left + frame.width * 0.32;
    const gy = top + frame.height * 0.3;
    const glint = ctx.createRadialGradient(gx, gy, 0, gx, gy, r);
    glint.addColorStop(0, `rgba(255, 255, 250, ${0.95 * look.sparkle})`);
    glint.addColorStop(0.5, `rgba(255, 252, 240, ${0.55 * look.sparkle})`);
    glint.addColorStop(1, "rgba(255, 250, 235, 0)");
    ctx.fillStyle = glint;
    ctx.beginPath();
    ctx.arc(gx, gy, r, 0, Math.PI * 2);
    ctx.fill();
    // A second, smaller glint below-right: two lights read as "alive".
    const r2 = r * 0.42;
    const hx = left + frame.width * 0.62;
    const hy = top + frame.height * 0.58;
    const glint2 = ctx.createRadialGradient(hx, hy, 0, hx, hy, r2);
    glint2.addColorStop(0, `rgba(255, 255, 250, ${0.6 * look.sparkle})`);
    glint2.addColorStop(1, "rgba(255, 250, 235, 0)");
    ctx.fillStyle = glint2;
    ctx.beginPath();
    ctx.arc(hx, hy, r2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // Eyelids: separate shapes erased out of the eye, so they can be
  // angled (tired/angry) or simply raised (happy).
  ctx.save();
  ctx.globalCompositeOperation = "destination-out";
  ctx.fillStyle = "#000";

  if (frame.tired > 0 || frame.angry > 0) {
    const lidHeight = frame.height * (frame.tired + frame.angry);
    // Tired droops the OUTER corner; angry droops the INNER corner.
    // `outerIsLeft` says which side of *this* eye is outer.
    const outerIsLeft = frame.outerIsLeft;
    const droopLeft = frame.tired > 0 ? outerIsLeft : !outerIsLeft;
    const apexX = droopLeft ? left : left + frame.width;
    ctx.beginPath();
    ctx.moveTo(left - 2, top - 2);
    ctx.lineTo(left + frame.width + 2, top - 2);
    ctx.lineTo(apexX, top + lidHeight);
    ctx.closePath();
    ctx.fill();
  }

  if (frame.happy > 0.005) {
    // A wide, shallow ellipse rising from below: its top edge is the
    // upward curve of a smiling eye, lowest at the corners.
    const offset = frame.height * frame.happy;
    const rx = frame.width * 0.85;
    const ry = frame.height * 0.75;
    ctx.beginPath();
    ctx.ellipse(left + frame.width / 2, top + frame.height - offset + ry, rx, ry, 0, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}

// Pink cheeks under an eye, a soft elliptical glow below its outer half.
// Drawn on the main canvas after the glow so the lids never cut them.
function drawBlush(ctx, eye, amount) {
  const outer = eye.outerIsLeft ? -1 : 1;
  const rx = eye.width * 0.36;
  const ry = rx * 0.42;
  const x = eye.x + outer * eye.width * 0.14;
  // Under the part of the eye that is still showing: a happy lid cuts
  // the bottom away, and cheeks under the hidden part float off.
  const y = eye.y + eye.height * (0.5 - eye.happy * 0.8) + ry * 1.2;
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(1, ry / rx);
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, rx);
  g.addColorStop(0, `rgba(255, 112, 140, ${0.55 * amount})`);
  g.addColorStop(0.6, `rgba(255, 120, 145, ${0.28 * amount})`);
  g.addColorStop(1, "rgba(255, 130, 150, 0)");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(0, 0, rx, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

export default class EyesFace {
  async mount(container) {
    container.innerHTML = "";
    // Two layers: the small glow canvas (with the ground colour) behind,
    // stretched by CSS, and the face canvas on top, transparent except
    // for what is drawn on it.
    const stack = document.createElement("div");
    stack.style.cssText = "position:relative;width:100%;height:100%;overflow:hidden";
    const glow = document.createElement("canvas");
    glow.style.cssText =
      `position:absolute;inset:0;width:100%;height:100%;display:block;background:${GROUND_COLOR}`;
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
    stack.appendChild(glow);
    stack.appendChild(canvas);
    container.appendChild(stack);

    this._canvas = canvas;
    this._ctx = canvas.getContext("2d");
    this._glow = glow;
    this._glowCtx = glow.getContext("2d");
    this._model = new RoboEyesModel(EYE_CONFIG);
    this._model.setMood(Mood.DEFAULT);
    this._model.setAutoblinker(true, 1, 4);
    this._model.setIdleMode(true, 4, 5);
    this._lastState = null;
    this._lastFrameAt = performance.now();
    this._ambient = new AmbientDirector({ minGapMs: 60000, maxGapMs: 120000 });
    this._floaters = new Floaters();
    this._following = false;
    this._emotion = null;
    this._emotionEntering = false;

    const motion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    this._reducedMotion = Boolean(motion && motion.matches);
    if (motion && motion.addEventListener) {
      motion.addEventListener("change", (event) => {
        this._reducedMotion = event.matches;
      });
    }

    this._resize = () => this._resizeCanvas();
    window.addEventListener("resize", this._resize);
    // The container changes size without the window doing so (a video
    // going fullscreen, a call panel opening): watch the box itself.
    if (window.ResizeObserver) {
      this._observer = new ResizeObserver(this._resize);
      this._observer.observe(container);
    }
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
    this._canvas.width = Math.max(1, Math.round(rect.width * dpr));
    this._canvas.height = Math.max(1, Math.round(rect.height * dpr));
    this._ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._glow.width = Math.max(1, Math.ceil(this._canvas.width / GLOW_DOWNSCALE));
    this._glow.height = Math.max(1, Math.ceil(this._canvas.height / GLOW_DOWNSCALE));
    this._widthCss = rect.width;
    this._heightCss = rect.height;
  }

  _draw(dtMs) {
    const ctx = this._ctx;
    const w = this._widthCss;
    const h = this._heightCss;

    ctx.clearRect(0, 0, w, h);
    if (!(w > 0 && h > 0)) return;

    const idle = this._lastState === "idle" || this._lastState === null;
    const visitor = this._ambient.tick(dtMs, {
      active: idle && !this._emotion && !this._reducedMotion && hasTheStage(w, window.innerWidth),
      width: w,
      height: h,
    });
    this._followVisitor(visitor, w, h, idle);
    if (!this._following && !this._emotion) this._idleFlourish(dtMs);

    const frame = this._model.tick(dtMs);
    const scale = fitScale(w, h);
    const cx = w / 2;
    const cy = h / 2;

    // Keep the pair inside the box: at this size the model's gaze range
    // would carry the eyes off the edge (eyes-layout.js).
    const halfW =
      (frame.right.x - frame.left.x) / 2 + Math.max(frame.left.width, frame.right.width) / 2;
    // Blushing cheeks hang below the eyes; keep room for them too.
    const tallest = Math.max(frame.left.height, frame.right.height);
    const halfH = tallest / 2 + (frame.blush > 0.01 ? tallest * 0.35 * frame.blush : 0);
    const { dx, dy } = clampPairOffset(
      (frame.left.x + frame.right.x) / 2,
      (frame.left.y + frame.right.y) / 2,
      halfW,
      halfH,
      scale,
      w,
      h
    );
    const left = { ...frame.left, x: frame.left.x + dx, y: frame.left.y + dy, outerIsLeft: true };
    const right = {
      ...frame.right,
      x: frame.right.x + dx,
      y: frame.right.y + dy,
      outerIsLeft: false,
    };
    const asleep = this._lastState === "sleeping";
    const look = { sparkle: frame.sparkle, brightness: frame.brightness, asleep };

    // Only the box around the eyes feeds the glow: a blur over the whole
    // layer every frame is the expensive part (GLOW_DOWNSCALE).
    const dpr = window.devicePixelRatio || 1;
    const blur = Math.min(48, left.width * scale * 0.3 * frame.brightness) * dpr;
    // A rectangle, not a square: the pair is three times wider than it is
    // tall, and the square cleared and copied ~3x the pixels (2026-10-08).
    // The tilt swings the outer corners up and down, so the height grows
    // by the width's share of it.
    const extentX = Math.max(Math.abs(left.x) + left.width, Math.abs(right.x) + right.width);
    const extentY = Math.max(Math.abs(left.y) + left.height, Math.abs(right.y) + right.height);
    const swing = Math.abs(Math.sin(frame.tilt));
    const reachX = (extentX + 8) * scale;
    const reachY = (extentY + extentX * swing + 8) * scale;
    const box = {
      x: Math.max(0, Math.floor((cx - reachX) * dpr)),
      y: Math.max(0, Math.floor((cy - reachY) * dpr)),
    };
    box.w = Math.min(this._canvas.width - box.x, Math.ceil(reachX * 2 * dpr));
    box.h = Math.min(this._canvas.height - box.y, Math.ceil(reachY * 2 * dpr));
    // Head tilt: rotate the pair about the face's centre, then scale the
    // model's units to this box.
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(frame.tilt);
    ctx.scale(scale, scale);
    drawEye(ctx, 0, 0, left, look);
    drawEye(ctx, 0, 0, right, look);
    ctx.restore();

    // The glow, from the finished eyes' own alpha, blurred small on the
    // layer behind. Cleared where it was drawn last frame, too: the eyes
    // move, and last frame's glow would trail behind them.
    if (box.w > 0 && box.h > 0) {
      const k = GLOW_DOWNSCALE;
      const pad = Math.ceil(blur * 1.5);
      const g = {
        x: Math.max(0, Math.floor((box.x - pad) / k)),
        y: Math.max(0, Math.floor((box.y - pad) / k)),
      };
      g.w = Math.min(this._glow.width - g.x, Math.ceil((box.w + pad * 2) / k) + 1);
      g.h = Math.min(this._glow.height - g.y, Math.ceil((box.h + pad * 2) / k) + 1);
      const glow = this._glowCtx;
      glow.setTransform(1, 0, 0, 1, 0, 0);
      const last = this._lastGlowBox;
      if (last) glow.clearRect(last.x, last.y, last.w, last.h);
      glow.clearRect(g.x, g.y, g.w, g.h);
      glow.shadowColor = `rgba(255, 176, 89, ${Math.min(0.85, (asleep ? 0.25 : 0.45) * frame.brightness)})`;
      glow.shadowBlur = blur / k;
      // Only the shadow lands on the layer: the shape is drawn far off to
      // the left and the shadow offset brings it back. A low-res copy of
      // the eye itself would show as a blocky rim around the sharp one.
      const away = this._glow.width + g.w + 64;
      glow.shadowOffsetX = away;
      glow.drawImage(this._canvas, box.x, box.y, box.w, box.h,
        box.x / k - away, box.y / k, box.w / k, box.h / k);
      glow.shadowOffsetX = 0;
      this._lastGlowBox = g;
    }

    // In front: the eyes look toward it, so they often end up near it,
    // and a ball vanishing behind an eye reads as a glitch.
    if (visitor) drawVisitor(ctx, visitor);

    if (frame.blush > 0.01 && !asleep) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(frame.tilt);
      ctx.scale(scale, scale);
      drawBlush(ctx, left, frame.blush);
      drawBlush(ctx, right, frame.blush);
      ctx.restore();
    }

    // z's while asleep, hearts for "love": rising from above the right eye.
    const hearts = this._emotion === "love";
    const floaters = this._floaters;
    floaters.everyMs = this._reducedMotion ? 3200 : hearts ? 1200 : 2200;
    const items = floaters.tick(dtMs, {
      active: asleep || hearts,
      originX: cx + (right.x + right.width * 0.45) * scale,
      originY: cy + (right.y - EYE_CONFIG.rightEye.height * 0.45) * scale,
      size: EYE_CONFIG.rightEye.height * 0.38 * scale,
      glyph: asleep ? "z" : "heart",
    });
    for (const item of items) drawFloater(ctx, item);
  }

  // The eyes watch a passing visitor; when it has gone they look back
  // and, as often as not, give a little happy squint about it.
  _followVisitor(visitor, w, h, idle) {
    const model = this._model;
    if (visitor) {
      if (!this._following) {
        this._following = true;
        model.setIdleMode(false);
      }
      const gaze = gazeToward(visitor.x, visitor.y, w, h, model.gazeRange);
      model.setGazeTarget(gaze.x, gaze.y);
      return;
    }
    if (!this._following) return;
    this._following = false;
    if (idle && !this._emotion) {
      model.setGazeTarget(0, 0);
      model.setIdleMode(true, 4, 5);
      if (Math.random() < 0.6) model.anim_squint(900);
    }
  }

  // While idle, every 15-30 s: a curious tilt or a happy squint. Rare
  // enough to feel like a personality, not a screensaver.
  _idleFlourish(dtMs) {
    if (this._lastState !== "idle" && this._lastState !== null) return;
    this._flourishInMs = (this._flourishInMs ?? randomBetween(15000, 30000)) - dtMs;
    if (this._flourishInMs > 0) return;
    this._flourishInMs = randomBetween(15000, 30000);
    if (Math.random() < 0.5) {
      this._model.anim_squint(1100);
    } else {
      this._model.setTiltTarget(randomBetween(0.04, 0.07) * (Math.random() < 0.5 ? -1 : 1));
      clearTimeout(this._untiltTimer);
      this._untiltTimer = setTimeout(() => {
        if (this._lastState === "idle" || this._lastState === null) this._model.setTiltTarget(0);
      }, 1400);
    }
  }

  onState(state) {
    if (!this._model) return;
    const entering = this._lastState !== state;
    // Asleep, the eyes are closed: there is no feeling left to show.
    if (state === "sleeping") this._clearEmotion();
    this._applyLook(state, entering);
  }

  // A feeling on top of the state's look, for `seconds`, then back to
  // the state alone. Unknown names and "neutral" clear it. Ignored while
  // asleep. Driven by core.py's side (screen/emotion.py), never by the
  // voice engine directly.
  onEmotion(name, seconds = 4) {
    if (!this._model) return;
    if (!EMOTION_NAMES.includes(name)) {
      if (this._emotion) {
        this._clearEmotion();
        this._applyLook(this._lastState, false);
      }
      return;
    }
    if (this._lastState === "sleeping") return;
    clearTimeout(this._emotionTimer);
    this._emotion = name;
    this._emotionEntering = true;
    this._applyLook(this._lastState, false);
    const ms = Math.max(500, Math.min(15000, Number(seconds) * 1000 || 4000));
    this._emotionTimer = setTimeout(() => {
      this._emotion = null;
      this._applyLook(this._lastState, false);
    }, ms);
  }

  _clearEmotion() {
    clearTimeout(this._emotionTimer);
    this._emotion = null;
  }

  _applyLook(state, entering) {
    const model = this._model;
    // Reset the per-state extras; each case below sets what it wants.
    model.setBlush(0);
    model.setSpeakingMotion(false);
    model.setAttentiveNods(false);
    model.setBreathing(false);
    model.setBlinkSpeed();
    model.setRoundness(1);
    model.setWarmth(0);
    model.setTiltTarget(0);
    model.setSparkle(0.5);
    model.setBrightness(1);
    model.setAutoblinker(true, 1, 4);

    switch (state) {
      case "sleeping":
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setAutoblinker(false);
        model.setGazeTarget(0, 0);
        model.setSizeScale(1);
        model.setSparkle(0);
        model.setBrightness(0.5);
        model.setBreathing(true, 0.04, 5);
        model.close();
        break;
      case "idle":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setSizeScale(1);
        model.setGazeTarget(0, 0);
        model.setIdleMode(true, 4, 5);
        model.setBreathing(true, 0.012, 4.5);
        break;
      case "attentive":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setGazeTarget(0, 0);
        model.setSizeScale(1.08);
        model.setRoundness(1.25);
        model.setWarmth(0.12);
        model.setSparkle(0.9);
        model.setBrightness(1.2);
        model.setTiltTarget(0.04);
        // Hold the eyes open through the perk: a blink here would eat it.
        model.setAutoblinker(true, 2.5, 3);
        if (entering) model.anim_perk();
        break;
      case "listening":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setGazeTarget(0, 0);
        model.setSizeScale(1.06);
        model.setRoundness(1.25);
        model.setWarmth(0.16);
        model.setSparkle(0.9);
        model.setBrightness(1.1);
        model.setBreathing(true, 0.015, 4.5);
        model.setAttentiveNods(true, 3, 4);
        model.setBlinkSpeed(6);
        model.setAutoblinker(true, 2.5, 3.5);
        break;
      case "thinking":
        model.open();
        model.setMood(Mood.TIRED);
        model.setSizeScale(1);
        model.setSparkle(0.35);
        {
          const { x, y } = model.gazeRange;
          if (entering) model.setGazeTarget(-x * 0.6, -y * 0.8);
        }
        // Wander along the top, always away from her.
        model.setIdleMode(true, 2, 2.5, { x: [-0.8, 0.8], y: [-0.9, -0.55] });
        model.setTiltTarget(-0.05);
        break;
      case "speaking":
        model.open();
        model.setMood(Mood.HAPPY);
        model.setIdleMode(false);
        model.setSizeScale(1.02);
        model.setRoundness(1.15);
        model.setSparkle(0.7);
        model.setBrightness(1.05);
        model.setGazeTarget(0, 0);
        model.setSpeakingMotion(true);
        break;
      case "handoff":
        model.open();
        model.setMood(Mood.DEFAULT);
        model.setIdleMode(false);
        model.setSizeScale(1);
        model.setGazeTarget(0, 0);
        if (entering) model.anim_confused();
        break;
      default:
        break;
    }
    if (this._emotion) {
      this._applyEmotion(this._emotion, this._emotionEntering);
      this._emotionEntering = false;
    }
    this._lastState = state;
  }

  // Each feeling is a handful of targets on the same eased model, so it
  // blends in and out of whatever the state was doing.
  _applyEmotion(name, entering) {
    const model = this._model;
    const { x, y } = model.gazeRange;
    switch (name) {
      case "happy":
        model.setMood(Mood.HAPPY);
        model.setSizeScale(1.08);
        model.setSparkle(1);
        model.setBrightness(1.3);
        if (entering) model.anim_laugh();
        break;
      case "blush":
        // Shy: cheeks warm up, a glance down and away, a little squint.
        model.setBlush(1);
        model.setWarmth(0.3);
        model.setSparkle(1);
        model.setIdleMode(false);
        model.setGazeTarget(x * 0.5, y * 0.1);
        model.setTiltTarget(0.09);
        if (entering) model.anim_squint(1200);
        break;
      case "love":
        model.setMood(Mood.HAPPY);
        model.setBlush(0.7);
        model.setSparkle(1);
        model.setBrightness(1.35);
        model.setIdleMode(false);
        model.setGazeTarget(0, 0);
        break;
      case "sad":
        // Outer corners droop, eyes a little smaller and dimmer, looking
        // down; the catchlight stays bright, which reads as glistening.
        model.setMood(Mood.TIRED);
        model.setSizeScale(0.9);
        model.setBrightness(0.7);
        model.setSparkle(0.85);
        model.setIdleMode(false);
        model.setGazeTarget(0, y * 0.25);
        model.setTiltTarget(-0.05);
        model.setBlinkSpeed(5);
        break;
      case "surprised":
        model.setMood(Mood.DEFAULT);
        model.setWarmth(0);
        model.setSizeScale(1.3);
        model.setRoundness(1.7);
        model.setSparkle(1);
        model.setBrightness(1.5);
        model.setIdleMode(false);
        model.setGazeTarget(0, 0);
        // Wide open: no blink to eat the surprise.
        model.setAutoblinker(true, 3, 2);
        if (entering) model.anim_perk();
        break;
      case "curious":
        model.setSizeScale(1.1);
        model.setSparkle(0.9);
        model.setIdleMode(false);
        model.setGazeTarget(x * 0.3, -y * 0.25);
        model.setTiltTarget(0.13);
        break;
      default:
        break;
    }
  }

  unmount() {
    this._running = false;
    clearTimeout(this._untiltTimer);
    clearTimeout(this._emotionTimer);
    window.removeEventListener("resize", this._resize);
    if (this._observer) this._observer.disconnect();
  }
}
