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
// she is listening (so the mic isn't fighting the speaker), held through
// thinking and speaking (so the reply is heard over it), back up at idle.
// 20% was chosen over mute so the room doesn't go dead-silent on every
// press — that silence reads as "it broke" — and over 50% because the
// AEC does not currently cover browser audio at all (see
// docs/completed/youtube.md, "AEC"), so what's left under her voice is
// what Whisper will hear.
//
// No PLAYING state exists in core.py and none is added here: playback is
// media state, not conversation state, and the face is driven by core
// alone (SPEC.md, "The face is driven by core.py").

export const DUCK_FACTOR = 0.2;

// `handoff` is the slower path's own thinking; it ducks like thinking.
// `attentive` is before she has said anything, so nothing is lowered.
export const DUCKED_STATES = ["listening", "thinking", "speaking", "handoff"];

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
