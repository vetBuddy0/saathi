// An ink blot that spreads and contracts. The third of the three faces
// SPEC.md asks for "so a real person can pick" — no vendored library, no
// external dependency, one element whose shape and color are driven by a
// per-state CSS class so the reaction to a new state starts on the next
// frame.

const CLASS_BY_STATE = {
  sleeping: "ink-face--sleeping",
  idle: "ink-face--idle",
  attentive: "ink-face--attentive",
  listening: "ink-face--listening",
  thinking: "ink-face--thinking",
  speaking: "ink-face--speaking",
  handoff: "ink-face--handoff",
};

export default class InkFace {
  async mount(container) {
    container.innerHTML = "";
    const blot = document.createElement("div");
    blot.className = "ink-face";
    container.appendChild(blot);
    this._blot = blot;
    this.onState("sleeping");
  }

  onState(state) {
    if (!this._blot) return;
    const stateClass = CLASS_BY_STATE[state] || CLASS_BY_STATE.idle;
    this._blot.className = `ink-face ${stateClass}`;
  }

  unmount() {
    this._blot = null;
  }
}
