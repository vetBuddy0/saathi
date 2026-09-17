// Bootstraps whichever Face was asked for (`?face=eyes|orb|ink`, default
// eyes), connects the WebSocket that carries core.py's state, and turns
// the spacebar into the two input events checkpoint 1 actually has:
// press and release. No status text is ever drawn here — CLAUDE.md, "a
// person doesn't display a status label."

import { FACE_MODULES, DEFAULT_FACE } from "./face.js";

function chosenFaceName() {
  const requested = new URLSearchParams(window.location.search).get("face");
  return requested && FACE_MODULES[requested] ? requested : DEFAULT_FACE;
}

async function main() {
  const faceName = chosenFaceName();
  const { default: FaceImpl } = await import(FACE_MODULES[faceName]);
  const face = new FaceImpl();
  await face.mount(document.getElementById("face-container"));

  const ws = new WebSocket(`ws://${window.location.host}/ws`);
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") {
      face.onState(message.state);
    }
  });

  let holding = false;
  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat || holding) return;
    event.preventDefault();
    holding = true;
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "input", event: "press" }));
    }
  });
  window.addEventListener("keyup", (event) => {
    if (event.code !== "Space" || !holding) return;
    event.preventDefault();
    holding = false;
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "input", event: "release" }));
    }
  });
}

main();
