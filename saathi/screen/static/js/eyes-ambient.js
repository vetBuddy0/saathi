// Little visitors for the eyes to watch, and the "z z z" of sleep.
//
// Why: the product owner (2026-10-07) asked for the face to be playful —
// now and then a ball rolls by, a leaf falls, a star drifts over, and
// the eyes follow it. A pet watching something cross the room is the
// most "alive" thing a face without a mouth can do, and it only happens
// while nothing else is going on (IDLE, with the face owning the
// screen), so it never competes with her.
//
// Rules this keeps:
//   - rare: one visitor at a time, 14-30 s apart, the first after 6-12 s;
//   - only while idle and on the full screen (eyes-layout.js's
//     hasTheStage); the moment she speaks, or a video or a call takes
//     the screen, the visitor is gone and the eyes look back at her;
//   - no words. "z" is drawn, not written: an animation, not a status
//     label (CLAUDE.md's rule is about labels describing the device);
//   - prefers-reduced-motion turns visitors off and slows the z's.
//
// Logic (where things are, when they come) is separate from drawing, and
// takes an injectable `random`, so tests/test_eyes_extras.py can step it
// headless. Drawing is plain canvas paths — no images, no fonts to load.

export const AMBIENT_KINDS = Object.freeze(["ball", "leaf", "star"]);

// Slow enough for the eyes to follow without darting (2026-10-09).
const DURATION_MS = { ball: 11000, leaf: 13000, star: 10000 };

function between(random, min, max) {
  return min + random() * (max - min);
}

// Where a visitor is at progress `t` (0..1) in a `width` x `height` box.
// Pure; `params` were rolled once at spawn.
export function positionAt(kind, params, t, width, height) {
  const size = Math.min(width, height);
  if (kind === "ball") {
    const r = size * 0.045;
    const fromX = params.dir > 0 ? -r : width + r;
    const toX = params.dir > 0 ? width + r : -r;
    const x = fromX + (toX - fromX) * t;
    // Rolls along the floor with two small hops on the way.
    const hop = Math.abs(Math.sin(t * Math.PI * 3)) * size * 0.03 * (1 - t * 0.5);
    const y = height - r - size * 0.05 - hop;
    return { x, y, size: r, rotation: ((x - fromX) / r) * params.dir, alpha: 1 };
  }
  if (kind === "leaf") {
    const s = size * 0.04;
    const y = -s * 2 + (height + s * 4) * t;
    const x = params.x0 * width + Math.sin(t * Math.PI * 2 * 1.3 + params.phase) * size * 0.05;
    const rotation = Math.sin(t * Math.PI * 2 * 1.3 + params.phase) * 0.7;
    return { x, y, size: s, rotation, alpha: 1 };
  }
  // star
  const s = size * 0.035;
  const fromX = params.dir > 0 ? -s * 2 : width + s * 2;
  const toX = params.dir > 0 ? width + s * 2 : -s * 2;
  const x = fromX + (toX - fromX) * t;
  const y = params.y0 * height + Math.sin(t * Math.PI) * size * 0.05;
  const twinkle = 0.75 + 0.25 * Math.sin(t * Math.PI * 14);
  return { x, y, size: s, rotation: t * Math.PI * 2 * params.dir, alpha: twinkle };
}

export class AmbientDirector {
  constructor({ random = Math.random, minGapMs = 14000, maxGapMs = 30000 } = {}) {
    this._random = random;
    this._minGapMs = minGapMs;
    this._maxGapMs = maxGapMs;
    this._untilNextMs = between(random, 6000, 12000);
    this._current = null; // {kind, params, ageMs, durationMs}
  }

  get current() {
    return this._current;
  }

  // Advances time. `active` is "idle and the face has the screen and
  // motion is welcome". Returns the visitor's drawable state, or null.
  // A visitor never outlives `active`: it is dropped, not finished.
  tick(dtMs, { active, width, height }) {
    if (!active) {
      this._current = null;
      // Don't arrive the instant she stops talking.
      this._untilNextMs = Math.max(this._untilNextMs, 4000);
      return null;
    }
    if (this._current === null) {
      this._untilNextMs -= dtMs;
      if (this._untilNextMs > 0) return null;
      this._current = this._spawn();
    }
    const visitor = this._current;
    visitor.ageMs += dtMs;
    if (visitor.ageMs >= visitor.durationMs) {
      this._current = null;
      this._untilNextMs = between(this._random, this._minGapMs, this._maxGapMs);
      return null;
    }
    const t = visitor.ageMs / visitor.durationMs;
    return { kind: visitor.kind, t, ...positionAt(visitor.kind, visitor.params, t, width, height) };
  }

  _spawn() {
    const random = this._random;
    const kind = AMBIENT_KINDS[Math.min(AMBIENT_KINDS.length - 1, Math.floor(random() * 3))];
    const dir = random() < 0.5 ? -1 : 1;
    const params = { dir };
    if (kind === "leaf") {
      // Down one side, clear of the eyes in the middle.
      params.x0 = dir < 0 ? between(random, 0.05, 0.12) : between(random, 0.88, 0.95);
      params.phase = between(random, 0, Math.PI * 2);
    } else if (kind === "star") {
      params.y0 = between(random, 0.06, 0.12);
    }
    return { kind, params, ageMs: 0, durationMs: DURATION_MS[kind] };
  }
}

// Rising glyphs: "z" while asleep, hearts for "love". Each floats up and
// to the side from `origin`, grows a little and fades.
export class Floaters {
  constructor({ everyMs = 1300, lifeMs = 3600 } = {}) {
    this.everyMs = everyMs;
    this.lifeMs = lifeMs;
    this._sinceMs = everyMs; // the first one comes straight away
    this._items = [];
    this._count = 0;
  }

  get items() {
    return this._items;
  }

  // `active`: keep making new ones (old ones always finish rising).
  tick(dtMs, { active, originX, originY, size, glyph = "z" }) {
    for (const item of this._items) item.ageMs += dtMs;
    this._items = this._items.filter((item) => item.ageMs < this.lifeMs);
    if (active) {
      this._sinceMs += dtMs;
      if (this._sinceMs >= this.everyMs) {
        this._sinceMs = 0;
        this._count += 1;
        this._items.push({ glyph, ageMs: 0, x0: originX, y0: originY, size, n: this._count });
      }
    } else {
      this._sinceMs = this.everyMs;
    }
    return this._items.map((item) => {
      const p = item.ageMs / this.lifeMs;
      return {
        glyph: item.glyph,
        x: item.x0 + p * item.size * 1.6 + Math.sin(item.ageMs / 450 + item.n) * item.size * 0.2,
        y: item.y0 - p * item.size * 3,
        fontSize: item.size * (0.55 + 0.65 * p),
        alpha: Math.sin(Math.PI * p),
      };
    });
  }
}

// -- drawing ----------------------------------------------------------------

export function drawVisitor(ctx, v) {
  ctx.save();
  ctx.globalAlpha = v.alpha;
  ctx.translate(v.x, v.y);
  ctx.rotate(v.rotation);
  if (v.kind === "ball") {
    const r = v.size;
    const body = ctx.createRadialGradient(-r * 0.35, -r * 0.35, r * 0.1, 0, 0, r);
    body.addColorStop(0, "#ffb39a");
    body.addColorStop(1, "#e2603f");
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.fill();
    // A stripe, so the roll is visible.
    ctx.strokeStyle = "rgba(255, 240, 220, 0.9)";
    ctx.lineWidth = r * 0.22;
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.62, -0.9, 0.9);
    ctx.stroke();
  } else if (v.kind === "leaf") {
    const s = v.size;
    const body = ctx.createLinearGradient(-s, 0, s, 0);
    body.addColorStop(0, "#e9a23b");
    body.addColorStop(1, "#c8642c");
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.moveTo(0, -s * 1.4);
    ctx.quadraticCurveTo(s * 1.1, -s * 0.2, 0, s * 1.4);
    ctx.quadraticCurveTo(-s * 1.1, -s * 0.2, 0, -s * 1.4);
    ctx.fill();
    ctx.strokeStyle = "rgba(120, 60, 20, 0.6)";
    ctx.lineWidth = s * 0.08;
    ctx.beginPath();
    ctx.moveTo(0, -s * 1.2);
    ctx.lineTo(0, s * 1.7);
    ctx.stroke();
  } else {
    const s = v.size;
    ctx.shadowColor = "rgba(255, 220, 140, 0.8)";
    ctx.shadowBlur = s * 1.2;
    ctx.fillStyle = "#ffe6a8";
    ctx.beginPath();
    for (let i = 0; i < 10; i += 1) {
      const radius = i % 2 === 0 ? s : s * 0.45;
      const a = (i / 10) * Math.PI * 2 - Math.PI / 2;
      ctx.lineTo(Math.cos(a) * radius, Math.sin(a) * radius);
    }
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

export function drawFloater(ctx, f) {
  if (f.alpha <= 0.01) return;
  ctx.save();
  ctx.globalAlpha = Math.min(1, f.alpha) * 0.9;
  if (f.glyph === "heart") {
    const s = f.fontSize * 0.5;
    ctx.fillStyle = "#ff8fa3";
    ctx.translate(f.x, f.y);
    ctx.beginPath();
    ctx.moveTo(0, s * 0.9);
    ctx.bezierCurveTo(-s * 1.4, -s * 0.1, -s * 0.6, -s * 1.1, 0, -s * 0.4);
    ctx.bezierCurveTo(s * 0.6, -s * 1.1, s * 1.4, -s * 0.1, 0, s * 0.9);
    ctx.fill();
  } else {
    ctx.fillStyle = "#ffce85";
    ctx.font = `600 ${Math.round(f.fontSize)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(f.glyph, f.x, f.y);
  }
  ctx.restore();
}
