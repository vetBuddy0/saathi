// The two decisions the media panel makes that are worth testing on
// their own, kept free of the DOM and the YouTube player so they can be
// run headless (tests/test_media_policy.py drives this module in a
// headless Chrome, since there is no node on the device or the dev box).
//
// Ducking, not pausing: a spacebar press during playback lowers the
// video, it never stops it. Pausing feels abrupt — she pressed to say
// something, not to end the song — and a paused video that has to be
// told to carry on turns every remark into two turns. The duck follows
// core.py's state, which the browser already receives: down the moment
// she is heard (so the mic isn't fighting the speaker), held through
// thinking and speaking (so the reply is heard over it), back up at idle.
//
// 2026-10-08, after live use: the song was being transcribed into her
// request. Two changes. `attentive` ducks too — it is the moment the
// wake word heard her name (her request is already arriving) and the
// whole open follow-up window after a reply (screen/server.py), not
// just a glance. And the duck is a tenth, not a fifth (20 dB down): a
// fifth still left the chorus louder than her voice at the mic. Mute
// still lost — the room going dead on every "Saathi" reads as "it
// broke" — and the echo canceller now hears the browser too (audio/
// aec.py, the default sink), so what is left under her voice is small.
//
// No PLAYING state exists in core.py and none is added here: playback is
// media state, not conversation state, and the face is driven by core
// alone (SPEC.md, "The face is driven by core.py").

export const DUCK_FACTOR = 0.1;

// `handoff` is the slower path's own thinking; it ducks like thinking.
export const DUCKED_STATES = ["attentive", "listening", "thinking", "speaking", "handoff"];

export function effectiveVolume(level, state) {
  const base = Math.max(0, Math.min(100, Number(level) || 0));
  if (DUCKED_STATES.includes(state)) {
    return Math.round(base * DUCK_FACTOR);
  }
  return base;
}

// Which classes `<body>` carries for a given panel view. The face is in
// every layout: beside the panel, or small in a corner when the video
// fills the screen. There is deliberately no layout without it.
//   view: "none" | "results" | "player"
export function layoutClasses(view, fullscreen) {
  if (view === "none") return [];
  if (view === "player" && fullscreen) return ["media--fullscreen"];
  return ["media--panel"];
}

export const ALL_LAYOUT_CLASSES = ["media--panel", "media--fullscreen"];
