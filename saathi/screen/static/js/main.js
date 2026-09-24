// Bootstraps whichever Face was asked for (`?face=eyes|orb|ink`, default
// eyes), connects the WebSocket that carries core.py's state, and turns
// the spacebar into the two input events checkpoint 1 actually has:
// press and release. No status text is ever drawn here — CLAUDE.md, "a
// person doesn't display a status label."
//
// Reconnects with backoff (checkpoint 2 robustness pass): systemd
// restarts saathi-engine.service by design (Restart=always), which
// closes this socket every time. The face's own idle animation (blink,
// drift) keeps running locally regardless — that part was never broken —
// so a dropped connection with no reconnect looked exactly like a
// healthy, idle device while actually being deaf to every keypress and
// blind to every real state change. Reconnecting is what fixes that; the
// console logging below is for whoever is debugging this on the device,
// not for her — it is diagnostic output, not a status label under the
// face, which CLAUDE.md rules out for a different reason (a person
// doesn't display one) than this one (a developer's terminal isn't the
// face).
//
// The media panel (media-panel.js, YouTube stream) draws beside the
// face what tools/media.py's controller tells it to — results, a
// player — and ducks the video on the state messages already flowing
// here. It adds nothing to the input path: space during playback is an
// ordinary press.
//
// Ctrl+L (settings-panel.js, item C/G) is the one deliberate exception
// to "the browser only carries spacebar events": a language/TTS-backend
// panel for whoever sets the device up, not for her — hidden until
// asked for, gone the moment it's closed, nothing persisted on the
// face. While it's open, a spacebar press is swallowed here rather than
// starting a capture underneath it.

import { FACE_MODULES, DEFAULT_FACE } from "./face.js";
import { createSettingsPanel } from "./settings-panel.js";
import { createMediaPanel } from "./media-panel.js";

const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 30000;

function chosenFaceName() {
  const requested = new URLSearchParams(window.location.search).get("face");
  return requested && FACE_MODULES[requested] ? requested : DEFAULT_FACE;
}

function connectWithReconnect(face, onMessage, isInputBlocked, onOpen) {
  let ws = null;
  let holding = false;
  let reconnectAttempt = 0;
  let reconnectTimer = null;

  function open() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);

    ws.addEventListener("open", () => {
      if (reconnectAttempt > 0) {
        console.info("saathi: reconnected to the engine");
      }
      reconnectAttempt = 0;
      if (onOpen) onOpen();
    });

    ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "state") {
        face.onState(message.state);
      }
      onMessage(message);
    });

    ws.addEventListener("close", () => {
      console.warn("saathi: connection to the engine dropped");
      scheduleReconnect();
    });

    // A WebSocket 'error' event is always followed by 'close' — no
    // separate handling needed here beyond not letting it go unlogged.
    ws.addEventListener("error", (event) => {
      console.error("saathi: websocket error", event);
    });
  }

  function scheduleReconnect() {
    if (reconnectTimer !== null) return; // already scheduled
    const delayMs = Math.min(
      RECONNECT_MAX_DELAY_MS,
      RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttempt
    );
    reconnectAttempt += 1;
    console.warn(`saathi: reconnecting in ${delayMs}ms (attempt ${reconnectAttempt})`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      open();
    }, delayMs);
  }

  open();

  function send(payload) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  }

  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat || holding || isInputBlocked()) return;
    event.preventDefault();
    holding = true;
    send({ type: "input", event: "press" });
  });
  window.addEventListener("keyup", (event) => {
    if (event.code !== "Space" || !holding) return;
    event.preventDefault();
    holding = false;
    send({ type: "input", event: "release" });
  });

  return { send };
}

async function main() {
  const faceName = chosenFaceName();
  const { default: FaceImpl } = await import(FACE_MODULES[faceName]);
  const face = new FaceImpl();
  await face.mount(document.getElementById("face-container"));

  let transport = null;
  const sendLater = (payload) => transport && transport.send(payload);
  const settingsPanel = createSettingsPanel(sendLater);
  // `?demo=media|media-playing|media-fullscreen` draws the panel with
  // sample content and no network, for looking at the layout. Dev
  // only; the kiosk never passes it.
  const demo = new URLSearchParams(window.location.search).get("demo");
  const mediaPanel = createMediaPanel(sendLater, { demo });
  transport = connectWithReconnect(
    face,
    (message) => {
      settingsPanel.onMessage(message);
      mediaPanel.onMessage(message);
    },
    // Not the media panel: space must keep working during playback --
    // a press ducks the video (media-panel.js), it never blocks input.
    () => settingsPanel.isOpen(),
    () => mediaPanel.onConnected()
  );
}

main();
