// Eyes, no mouth (SPEC.md, "The face"). Wraps the vendored
// Web-Eye-Animation library unmodified; this file is the adapter from
// core.py's states to its `window.eyes` API.
//
// Approximate v1 mapping — the library ships ten fixed emotions and no
// "listening" or "thinking" concept, so this leans on the two spatial cues
// SPEC.md calls out instead: `target()` for "gaze toward her" (idle,
// listening, speaking) and `emotion("confusion")` for "looking away while
// thinking" is the closest available approximation until a purpose-built
// eyes face replaces the vendored one.

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.appendChild(script);
  });
}

const EMOTION_BY_STATE = {
  sleeping: "sleepy",
  attentive: "surprise",
  thinking: "confusion",
  handoff: "confusion",
  speaking: "joy",
};

export default class EyesFace {
  async mount(container) {
    container.innerHTML = "";
    const eyeContainer = document.createElement("div");
    eyeContainer.className = "eye-container";
    container.appendChild(eyeContainer);

    const vendorUrl = new URL("../vendor/web-eye-animation/web-eye-animation.js", import.meta.url);
    await loadScript(vendorUrl.href);

    // Forces the library's lazy GSAP load to happen now, during mount(),
    // not on the first real onState() call — see the 100ms note in
    // face.js.
    if (window.eyes) {
      await window.eyes.target(0, 0, 1000);
    }
  }

  onState(state) {
    if (!window.eyes) return;
    if (state === "idle" || state === "listening") {
      window.eyes.target(0, 0, 1000); // gaze toward her
      return;
    }
    const emotion = EMOTION_BY_STATE[state];
    if (emotion) window.eyes.emotion(emotion);
  }

  unmount() {}
}
