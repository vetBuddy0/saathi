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
  }

  step(dtSeconds) {
    this.width = approach(this.width, this.widthTarget, dtSeconds);
    this.height = approach(this.height, this.heightTarget, dtSeconds);
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
      if (closed) this.heightTarget = this.heightDefault;
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
    for (const eye of [this.left, this.right]) {
      eye.tiredTarget = mood === Mood.TIRED ? 0.5 : 0;
      eye.angryTarget = mood === Mood.ANGRY ? 0.5 : 0;
      eye.happyTarget = mood === Mood.HAPPY ? 0.55 : 0;
    }
  }

  // -- size / gaze ---------------------------------------------------------

  // `scale` widens or narrows both eyes around their default size — used
  // for ATTENTIVE ("widens") and LISTENING ("slightly wider").
  setSizeScale(scale) {
    for (const eye of [this.left, this.right]) {
      eye.widthTarget = eye.widthDefault * scale;
      eye.heightTarget = eye.open === false ? eye.heightTarget : eye.heightDefault * scale;
    }
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
      this.left.heightTarget = this.left.heightDefault;
    }
    if (this.right.height <= this.right.heightDefault * 0.08) {
      this.right.heightTarget = this.right.heightDefault;
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

  setIdleMode(active, minIntervalSeconds = 1, variationSeconds = 3) {
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
      this.setGazeTarget(
        randomBetween(-this._maxGazeX, this._maxGazeX),
        randomBetween(-this._maxGazeY, this._maxGazeY)
      );
      this._idle.nextAt =
        this._clockMs + this._idle.minMs + Math.random() * this._idle.variationMs;
    }

    this.left.step(dtSeconds);
    this.right.step(dtSeconds);

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
      shakeY += Math.sin(this._clockMs / 220) * 2.5;
    }

    return {
      left: this._frameFor(this.left, shakeX, shakeY),
      right: this._frameFor(this.right, shakeX, shakeY),
    };
  }

  _frameFor(eye, shakeX, shakeY) {
    return {
      x: eye.x + shakeX,
      y: eye.y + shakeY,
      width: eye.width,
      height: eye.height,
      borderRadius: eye.borderRadius,
      tired: eye.tired,
      angry: eye.angry,
      happy: eye.happy,
    };
  }
}
