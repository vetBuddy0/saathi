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
