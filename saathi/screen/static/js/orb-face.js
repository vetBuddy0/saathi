// A single glowing orb. SPEC.md argues against this shape for the reason
// it exists here anyway: "an orb cannot look at someone... that decision
// isn't ours to make from intuition." No vendored library, no external
// CDN dependency — color and a pulse, driven entirely by CSS transitions
// so the reaction to a new state starts on the next frame.

const COLOR_BY_STATE = {
  sleeping: "#1c2230",
  idle: "#3a4a63",
  attentive: "#5b7fd6",
  listening: "#7fb0ff",
  thinking: "#caa6ff",
  speaking: "#ffd27f",
  handoff: "#ff9f7f",
};

export default class OrbFace {
  async mount(container) {
    container.innerHTML = "";
    const orb = document.createElement("div");
    orb.className = "orb-face";
    container.appendChild(orb);
    this._orb = orb;
    this.onState("sleeping");
  }

  onState(state) {
    if (!this._orb) return;
    const color = COLOR_BY_STATE[state] || COLOR_BY_STATE.idle;
    this._orb.style.setProperty("--orb-color", color);
    this._orb.classList.toggle(
      "orb-face--active",
      state === "listening" || state === "speaking" || state === "attentive"
    );
  }

  unmount() {
    this._orb = null;
  }
}
