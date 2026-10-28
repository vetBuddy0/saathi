// The `Face` interface (SPEC.md, "The five interfaces" — v1
// implementations: eyes, orb, ink). It lives in the browser, not Python,
// because the face renders in the browser; `screen/server.py` only ever
// relays state, it never renders one.
//
// A Face is any class with:
//   async mount(container: HTMLElement): resolves once ready to receive
//     onState() calls. Do any slow setup here (loading a vendor script,
//     preloading GSAP) — never lazily on the first onState(), which is the
//     call that has to land inside the 100ms budget.
//   onState(state: string): react to a new core.py State. Must be fast:
//     start the visual change synchronously or on the next frame; a
//     multi-hundred-ms animation *playing* is fine, only its *start* is
//     budgeted.
//   unmount(): tear down, if the face is ever swapped at runtime.
//   onEmotion(name, seconds) — OPTIONAL (2026-10-07): a short-lived
//     feeling (blush, happy, love, sad, surprised, curious; "neutral"
//     clears) layered on the current state and lapsing back to it after
//     `seconds`. Sent by screen/emotion.py on core's side, never by the
//     engine. Optional so the orb and ink faces stay valid unchanged —
//     main.js only calls it where it exists. Size comes from the
//     container (it can shrink beside a video or a call), so a face
//     must draw to its container's box, not the window's.
//
// Three faces exist "so a real person can pick — that decision isn't ours
// to make from intuition" (SPEC.md). Nothing else in this codebase should
// assume which one is active.

export const FACE_MODULES = {
  eyes: "./eyes-face.js",
  orb: "./orb-face.js",
  ink: "./ink-face.js",
};

export const DEFAULT_FACE = "eyes";
