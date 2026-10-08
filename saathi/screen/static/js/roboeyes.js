// A from-scratch reimplementation of FluxGarage/RoboEyes' animation
// *model* — studied at /tmp/RoboEyes (GPL-3.0) for its geometry and timing
// relationships, no source from that project reproduced here. RoboEyes
// targets a monochrome OLED with Adafruit_GFX; this file has no display
// dependency at all — it produces plain numbers, and `eyes-face.js` is the
// only thing that knows about a canvas or a colour.
//
// The model this reimplements: two eyes, each a rounded rectangle defined
// by width/height/borderRadius, spaced by `spaceBetween` — "everything
// else derives from those four." Moods change eyelid *targets*, not eye
// geometry: DEFAULT draws no eyelids, TIRED and ANGRY grow a diagonal top
// eyelid, HAPPY grows a rounded bottom eyelid. Every current value chases
// its target by the same law, once per tick — that single mechanism is
// what makes blinking, moving, resizing and mood changes all animate
// smoothly for free, and it is the one piece of RoboEyes worth being
// faithful to beyond the specific features it names.
//
// Two differences from the studied model, both deliberate:
//   - RoboEyes eases by a fixed per-frame ratio (assumes ~50fps). This
//     reimplementation eases by an exponential decay against actual
//     elapsed time (`_approach`), so it looks the same at 30fps or 144fps.
//   - `setSpeakingMotion` (a continuous, small vertical bob) has no
//     RoboEyes equivalent — everything there is either steady or a
//     one-shot macro (confused, laugh). SPEAKING needs a *sustained* cue,
//     so this is new, kept clearly separate from the reimplemented part.
//
// Expressiveness layer (2026-10-07), also not RoboEyes: the first live
// demo read the listening eyes as "a held pose", not "paying attention".
// A pet or a small child listening is never still: they breathe, tilt,
// nod a little and their eyes catch the light. So, all on top of the
// target/ease model and never replacing it:
//   - breathing: a slow, small size oscillation applied to the *frame*,
//     not the targets, so it can't fight a blink or a resize;
//   - tilt: the pair rotates a few degrees about its centre (a head
//     tilt), eased like everything else;
//   - nods: occasional small tilt/drop "mm-hm"s while listening;
//   - sparkle and brightness: how strong the catchlight and glow are —
//     numbers only, `eyes-face.js` decides what they look like;
//   - warmth: a low HAPPY lid that layers on any mood (SPEC.md's
//     "narrowing at the corners for warmth") without changing the mood;
//   - perk: a one-shot "oh — you called me?" hop and widen, for the
//     wake word arriving (ATTENTIVE);
//   - squint: a brief happy squint, used by idle now and then;
//   - blink speed: listening blinks slowly, which reads as calm trust;
//     a fast blink there read as flinching;
//   - idle region: idle drift can be confined (THINKING wanders along
//     the top, looking away; IDLE uses the whole range).
// The option that lost was CSS transforms on the canvas element: they
// would bypass the model's easing and could not be seen by its tests.
//
// Blush (2026-10-07, owner's request for a playful face): one more eased
// number, 0..1. The model only says how much; `eyes-face.js` draws the
// pink cheeks under the eyes. No eyebrows and no mouth were added.

export const Mood = Object.freeze({
  DEFAULT: "default",
  TIRED: "tired",
  ANGRY: "angry",
  HAPPY: "happy",
});

// How quickly a "current" value closes the gap to its "target" per
// second. 1 - exp(-RATE * dt) is the fraction of the remaining gap
// closed in `dt` seconds; ~12 gives a snappy but not instant settle.
const EASE_RATE = 12;

const PERK_MS = 800;
const NOD_MS = 520;

function approach(current, target, dtSeconds, rate = EASE_RATE) {
  const factor = 1 - Math.exp(-rate * dtSeconds);
  return current + (target - current) * factor;
}

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

class Eye {
  constructor({ width, height, borderRadius }, defaultX) {
    this.widthDefault = width;
    this.heightDefault = height;
    this.borderRadiusDefault = borderRadius;

    this.width = width;
    this.height = height;
    this.borderRadius = borderRadius;
    this.widthTarget = width;
    this.heightTarget = height;
    this.borderRadiusTarget = borderRadius;

    // x/y are offsets from the model's gaze point, not screen coordinates.
    this.defaultX = defaultX;
    this.x = defaultX;
    this.y = 0;
    this.xTarget = defaultX;
    this.yTarget = 0;

    // Eyelid coverage, 0..1 of this eye's own height. Only one of
    // tired/angry is ever non-zero — happy is independent (SPEC.md: HAPPY
    // is the only mood that reads as warmth, so it can layer on nothing
    // else, but tired and angry are mutually exclusive top-lid shapes).
    this.tired = 0;
    this.angry = 0;
    this.happy = 0;
    this.tiredTarget = 0;
    this.angryTarget = 0;
    this.happyTarget = 0;

    this.open = true;
    // Only the height (what a blink moves) gets its own rate, so a slow
    // blink doesn't also make every resize sluggish.
    this.heightRate = EASE_RATE;
    // The height an open eye rests at: the default times the current
    // size scale. A blink reopens to this, not to the default — before,
    // a blink while LISTENING snapped the eyes back to idle size.
    this.heightOpen = height;
  }

  step(dtSeconds) {
    this.width = approach(this.width, this.widthTarget, dtSeconds);
    this.height = approach(this.height, this.heightTarget, dtSeconds, this.heightRate);
    this.borderRadius = approach(this.borderRadius, this.borderRadiusTarget, dtSeconds);
    this.x = approach(this.x, this.xTarget, dtSeconds);
    this.y = approach(this.y, this.yTarget, dtSeconds);
    this.tired = approach(this.tired, this.tiredTarget, dtSeconds);
    this.angry = approach(this.angry, this.angryTarget, dtSeconds);
    this.happy = approach(this.happy, this.happyTarget, dtSeconds);

    // A blink is just "closing" (heightTarget forced near zero) followed
    // by "reopening" once the close has actually finished — same
    // mechanism RoboEyes uses so the reopen never overlaps a close that
    // hasn't visually landed yet.
    if (this.open && this.heightTarget < this.heightDefault * 0.5) {
      const closed = this.height <= this.heightDefault * 0.08;
      if (closed) this.heightTarget = this.heightOpen;
    }
  }
}

export class RoboEyesModel {
  constructor({ leftEye, rightEye, spaceBetween }) {
    const totalWidth = leftEye.width + spaceBetween + rightEye.width;
    const leftDefaultX = -totalWidth / 2 + leftEye.width / 2;
    const rightDefaultX = leftDefaultX + leftEye.width / 2 + spaceBetween + rightEye.width / 2;

    this.left = new Eye(leftEye, leftDefaultX);
    this.right = new Eye(rightEye, rightDefaultX);
    this.mood = Mood.DEFAULT;

    // Derived purely from eye size: how far gaze is allowed to drift, and
    // how far a blink/mood eyelid can reach into the eye.
    this._maxGazeX = totalWidth * 0.35;
    this._maxGazeY = Math.max(leftEye.height, rightEye.height) * 1.1;

    this._autoblink = null; // {minMs, variationMs, nextAt}
    this._idle = null; // {minMs, variationMs, nextAt}
    this._clockMs = 0;

    this._confusedUntilMs = null;
    this._laughUntilMs = null;
    this._speaking = false;

    // Expressiveness layer — see module docstring.
    this._sizeScale = 1;
    this._roundness = 1;
    this._warmth = 0;
    this._breathing = null; // {amplitude, periodMs}
    this._nods = null; // {minMs, variationMs, nextAt}
    this._nodUntilMs = null;
    this._idleRegion = null; // {x: [min, max], y: [min, max]} as fractions of range
    this._perkUntilMs = null;
    this._squintUntilMs = null;
    this._squintMs = 0;
    this.tilt = 0; // radians, eased
    this.tiltTarget = 0;
    this.sparkle = 0.5;
    this.sparkleTarget = 0.5;
    this.brightness = 1;
    this.brightnessTarget = 1;
    this.blush = 0;
    this.blushTarget = 0;
  }

  // How far `setGazeTarget` will actually move the eyes, derived from eye
  // size — public so callers can aim at "the edge of the gaze range"
  // (e.g. THINKING's "looks away and up") without reaching into internals.
  get gazeRange() {
    return { x: this._maxGazeX, y: this._maxGazeY };
  }

  // -- moods -------------------------------------------------------------

  setMood(mood) {
    this.mood = mood;
    this._applyLids();
  }

  // A low happy lid that layers on any mood, 0..1 of eye height. 0 is
  // off; HAPPY's own 0.55 still wins when it's larger.
  setWarmth(amount) {
    this._warmth = Math.max(0, Math.min(0.5, amount));
    this._applyLids();
  }

  _applyLids() {
    const mood = this.mood;
    for (const eye of [this.left, this.right]) {
      eye.tiredTarget = mood === Mood.TIRED ? 0.5 : 0;
      eye.angryTarget = mood === Mood.ANGRY ? 0.5 : 0;
      eye.happyTarget = Math.max(mood === Mood.HAPPY ? 0.55 : 0, this._warmth);
    }
  }

  // -- size / gaze ---------------------------------------------------------

  // `scale` widens or narrows both eyes around their default size — used
  // for ATTENTIVE ("widens") and LISTENING ("slightly wider").
  setSizeScale(scale) {
    this._sizeScale = scale;
    for (const eye of [this.left, this.right]) {
      eye.widthTarget = eye.widthDefault * scale;
      eye.heightOpen = eye.heightDefault * scale;
      // Mid-blink (target below half) or closed: leave the lid alone;
      // the reopen picks up heightOpen.
      const blinking = eye.heightTarget < eye.heightDefault * 0.5;
      if (eye.open !== false && !blinking) eye.heightTarget = eye.heightOpen;
      eye.borderRadiusTarget = eye.borderRadiusDefault * scale * this._roundness;
    }
  }

  // Corner radius relative to the default; >1 is rounder, softer, and
  // reads younger. Capped by the renderer at half the eye's size.
  setRoundness(factor) {
    this._roundness = factor;
    this.setSizeScale(this._sizeScale);
  }

  // Head tilt in radians, positive = clockwise. Kept small: past ~0.12
  // it reads as quizzical rather than attentive.
  setTiltTarget(radians) {
    this.tiltTarget = Math.max(-0.2, Math.min(0.2, radians));
  }

  // Strength of the catchlight, 0..1. Brighter while she has its
  // attention — the "eyes light up" cue.
  setSparkle(amount) {
    this.sparkleTarget = Math.max(0, Math.min(1, amount));
  }

  // Glow multiplier, ~0.5 (asleep) .. ~1.4 (just called).
  setBrightness(amount) {
    this.brightnessTarget = Math.max(0, Math.min(2, amount));
  }

  // Pink cheeks, 0..1. Eases in slowly (a blush rises, it doesn't snap).
  setBlush(amount) {
    this.blushTarget = Math.max(0, Math.min(1, amount));
  }

  // A slow size oscillation, `amplitude` as a fraction of size.
  setBreathing(active, amplitude = 0.025, periodSeconds = 3.4) {
    this._breathing = active ? { amplitude, periodMs: periodSeconds * 1000 } : null;
  }

  // How fast a blink closes and reopens; the default is EASE_RATE.
  setBlinkSpeed(rate = EASE_RATE) {
    this.left.heightRate = rate;
    this.right.heightRate = rate;
  }

  // Gaze target in the same units as eye width/height, (0, 0) = dead
  // ahead ("gaze toward her"). Positive x is the viewer's right; positive
  // y is down.
  setGazeTarget(x, y) {
    const clampedX = Math.max(-this._maxGazeX, Math.min(this._maxGazeX, x));
    const clampedY = Math.max(-this._maxGazeY, Math.min(this._maxGazeY, y));
    this.left.xTarget = this.left.defaultX + clampedX;
    this.right.xTarget = this.right.defaultX + clampedX;
    this.left.yTarget = clampedY;
    this.right.yTarget = clampedY;
  }

  // -- eyelids (open/closed) ----------------------------------------------

  close() {
    this.left.open = false;
    this.right.open = false;
    this.left.heightTarget = this.left.heightDefault * 0.05;
    this.right.heightTarget = this.right.heightDefault * 0.05;
  }

  open() {
    this.left.open = true;
    this.right.open = true;
    // Only actually widens once the eye has finished closing — see
    // Eye.step(); setting the target early would skip the close.
    if (this.left.height <= this.left.heightDefault * 0.08) {
      this.left.heightTarget = this.left.heightOpen;
    }
    if (this.right.height <= this.right.heightDefault * 0.08) {
      this.right.heightTarget = this.right.heightOpen;
    }
  }

  blink() {
    this.close();
    this.open();
  }

  // -- automated behaviours -------------------------------------------

  setAutoblinker(active, minIntervalSeconds = 1, variationSeconds = 4) {
    if (!active) {
      this._autoblink = null;
      return;
    }
    this._autoblink = {
      minMs: minIntervalSeconds * 1000,
      variationMs: variationSeconds * 1000,
      nextAt: this._clockMs + minIntervalSeconds * 1000,
    };
  }

  // `region` confines where idle drift wanders, as fractions of the
  // gaze range: {x: [-1, 1], y: [-1, 1]} is everywhere (the default).
  setIdleMode(active, minIntervalSeconds = 1, variationSeconds = 3, region = null) {
    this._idleRegion = region;
    if (!active) {
      this._idle = null;
      return;
    }
    this._idle = {
      minMs: minIntervalSeconds * 1000,
      variationMs: variationSeconds * 1000,
      nextAt: this._clockMs + minIntervalSeconds * 1000,
    };
  }

  // -- one-shot macro animations ----------------------------------------

  anim_confused() {
    this._confusedUntilMs = this._clockMs + 500;
  }

  anim_laugh() {
    this._laughUntilMs = this._clockMs + 500;
  }

  // "Oh — you called me?": a quick hop up and a widen that overshoots
  // and settles. Not RoboEyes.
  anim_perk() {
    this._perkUntilMs = this._clockMs + PERK_MS;
  }

  // A brief happy squint layered over whatever the mood is. Not RoboEyes.
  anim_squint(ms = 900) {
    this._squintMs = ms;
    this._squintUntilMs = this._clockMs + ms;
  }

  // Occasional small "mm-hm" nods and tilts while listening. Not RoboEyes.
  setAttentiveNods(active, minIntervalSeconds = 1.4, variationSeconds = 1.8) {
    if (!active) {
      this._nods = null;
      this._nodUntilMs = null;
      return;
    }
    this._nods = {
      minMs: minIntervalSeconds * 1000,
      variationMs: variationSeconds * 1000,
      nextAt: this._clockMs + minIntervalSeconds * 1000,
    };
  }

  // Not part of the reimplemented RoboEyes model — see module docstring.
  setSpeakingMotion(active) {
    this._speaking = active;
  }

  // -- advance one frame ---------------------------------------------------

  tick(dtMs) {
    const dtSeconds = Math.min(dtMs, 100) / 1000; // clamp huge gaps (tab away)
    this._clockMs += dtMs;

    if (this._autoblink && this._clockMs >= this._autoblink.nextAt) {
      this.blink();
      this._autoblink.nextAt =
        this._clockMs + this._autoblink.minMs + Math.random() * this._autoblink.variationMs;
    }

    if (this._idle && this._clockMs >= this._idle.nextAt) {
      const region = this._idleRegion || { x: [-1, 1], y: [-1, 1] };
      this.setGazeTarget(
        randomBetween(region.x[0], region.x[1]) * this._maxGazeX,
        randomBetween(region.y[0], region.y[1]) * this._maxGazeY
      );
      this._idle.nextAt =
        this._clockMs + this._idle.minMs + Math.random() * this._idle.variationMs;
    }

    if (this._nods && this._clockMs >= this._nods.nextAt) {
      // Alternate sides at random; never a big swing.
      this.setTiltTarget(randomBetween(0.03, 0.06) * (Math.random() < 0.5 ? -1 : 1));
      this._nodUntilMs = this._clockMs + NOD_MS;
      this._nods.nextAt =
        this._clockMs + this._nods.minMs + Math.random() * this._nods.variationMs;
    }

    this.left.step(dtSeconds);
    this.right.step(dtSeconds);
    this.tilt = approach(this.tilt, this.tiltTarget, dtSeconds, 5);
    this.sparkle = approach(this.sparkle, this.sparkleTarget, dtSeconds, 6);
    this.brightness = approach(this.brightness, this.brightnessTarget, dtSeconds, 8);
    this.blush = approach(this.blush, this.blushTarget, dtSeconds, 4);

    let scale = 1;
    let happyExtra = 0;

    let shakeX = 0;
    let shakeY = 0;

    if (this._confusedUntilMs !== null) {
      const remaining = this._confusedUntilMs - this._clockMs;
      if (remaining <= 0) {
        this._confusedUntilMs = null;
      } else {
        // Decaying horizontal oscillation, not RoboEyes' raw alternating
        // offset (see module docstring) — same "shake left and right" cue.
        const envelope = remaining / 500;
        shakeX = Math.sin(this._clockMs / 30) * 12 * envelope;
      }
    }

    if (this._laughUntilMs !== null) {
      const remaining = this._laughUntilMs - this._clockMs;
      if (remaining <= 0) {
        this._laughUntilMs = null;
      } else {
        const envelope = remaining / 500;
        shakeY = Math.sin(this._clockMs / 25) * 8 * envelope;
      }
    }

    if (this._speaking) {
      // Two incommensurate waves: a lively bob that never visibly loops,
      // plus a small squash that loosely suggests syllables.
      shakeY += Math.sin(this._clockMs / 420) * 1.2 + Math.sin(this._clockMs / 180) * 0.5;
      scale *= 1 - Math.max(0, Math.sin(this._clockMs / 240)) * 0.015;
    }

    if (this._breathing) {
      const phase = (this._clockMs / this._breathing.periodMs) * 2 * Math.PI;
      scale *= 1 + Math.sin(phase) * this._breathing.amplitude;
    }

    if (this._nodUntilMs !== null) {
      const remaining = this._nodUntilMs - this._clockMs;
      if (remaining <= 0) {
        this._nodUntilMs = null;
        // Settle back towards upright, not all the way: a listener's
        // head rarely ends perfectly level.
        this.setTiltTarget(this.tiltTarget * 0.3);
      } else {
        // A small dip and back: half a sine over the nod.
        shakeY += Math.sin(Math.PI * (1 - remaining / NOD_MS)) * 3;
      }
    }

    if (this._perkUntilMs !== null) {
      const remaining = this._perkUntilMs - this._clockMs;
      if (remaining <= 0) {
        this._perkUntilMs = null;
      } else {
        const t = 1 - remaining / PERK_MS; // 0 -> 1
        // A hop up, then a little overshoot down, then rest.
        shakeY += -Math.sin(Math.PI * Math.min(1, t * 1.6)) * 10 * (1 - t);
        scale *= 1 + Math.sin(Math.PI * t) * 0.08 * (1 - t * 0.5);
      }
    }

    if (this._squintUntilMs !== null) {
      const remaining = this._squintUntilMs - this._clockMs;
      if (remaining <= 0) {
        this._squintUntilMs = null;
      } else {
        happyExtra = Math.sin(Math.PI * (1 - remaining / this._squintMs)) * 0.32;
      }
    }

    return {
      left: this._frameFor(this.left, shakeX, shakeY, scale, happyExtra),
      right: this._frameFor(this.right, shakeX, shakeY, scale, happyExtra),
      tilt: this.tilt,
      sparkle: this.sparkle,
      brightness: this.brightness,
      blush: this.blush,
    };
  }

  _frameFor(eye, shakeX, shakeY, scale = 1, happyExtra = 0) {
    return {
      x: eye.x + shakeX,
      y: eye.y + shakeY,
      width: eye.width * scale,
      height: eye.height * scale,
      borderRadius: eye.borderRadius * scale,
      tired: eye.tired,
      angry: eye.angry,
      happy: Math.min(0.7, eye.happy + happyExtra),
    };
  }
}
