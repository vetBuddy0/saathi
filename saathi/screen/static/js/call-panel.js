// The phone panel: while a call is on, the right of the screen shows who
// is on the line, their number, how long it has been going, whether it
// has been answered, and one big red "End call" button. The face moves
// to the left and is drawn to fit there (eyes-layout.js); it is never
// hidden.
//
// Why: a call used to be invisible — the face looked exactly as it does
// between turns — and the only way to hang up was a two-second spacebar
// hold she had to be told about. The hold still works; the button is the
// obvious way.
//
// The browser decides nothing. The panel is drawn from the server's
// `call` message (screen/call_panel.py, fed by call/controller.py) and
// the button only sends `{type: "call_hangup", id}`; the server checks
// the id and fires the hold seam's registered hang-up — the same one the
// spacebar hold fires. The timer is counted here from `elapsed_seconds`
// so nothing ticks over the socket.
//
// The status word ("Calling", "Ringing", "Connected") is a phone's own
// screen beside the face, not a status label under it — see
// screen/call_panel.py for why it lost to "no word at all".

const RETRY_AFTER_MS = 3000;

const STATUS_WORDS = { calling: "Calling", ringing: "Ringing", connected: "Connected" };

const DEMO = {
  "call-calling": {
    id: "demo-call",
    name: "Priya",
    number: "+65 9123 4567",
    status: "ringing",
    elapsed_seconds: null,
  },
  "call-connected": {
    id: "demo-call",
    name: "Priya",
    number: "+65 9123 4567",
    status: "connected",
    elapsed_seconds: 83,
  },
};

// 0:07, 1:23, 12:05, 1:02:09 — never negative, never NaN.
export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${ss}`;
  return `${m}:${ss}`;
}

export function statusWord(status) {
  return STATUS_WORDS[status] || "";
}

export function createCallPanel(send, options = {}) {
  const now = options.now || (() => performance.now());

  const root = document.createElement("div");
  root.id = "call-panel";
  root.hidden = true;
  root.innerHTML = `
    <div class="call-panel__who">
      <div class="call-panel__name"></div>
      <div class="call-panel__number"></div>
    </div>
    <div class="call-panel__status"></div>
    <div class="call-panel__timer"></div>
    <button type="button" class="call-panel__end">End call</button>
  `;
  document.body.appendChild(root);
  const nameEl = root.querySelector(".call-panel__name");
  const numberEl = root.querySelector(".call-panel__number");
  const statusEl = root.querySelector(".call-panel__status");
  const timerEl = root.querySelector(".call-panel__timer");
  const endButton = root.querySelector(".call-panel__end");

  let call = null;
  let connectedAt = null; // now() - elapsed, when connected
  let timer = null;
  let ended = false; // the tap for this call was sent

  function renderTimer() {
    timerEl.textContent =
      connectedAt === null ? "" : formatDuration((now() - connectedAt) / 1000);
  }

  function stopTimer() {
    if (timer !== null) clearInterval(timer);
    timer = null;
  }

  function show(next) {
    const sameCall = call !== null && next !== null && call.id === next.id;
    if (!sameCall) ended = false;
    call = next;
    if (call === null) {
      stopTimer();
      connectedAt = null;
      root.hidden = true;
      document.body.classList.remove("call--open");
      return;
    }
    nameEl.textContent = call.name || call.number || "";
    // The number under the name only when there is a name above it.
    numberEl.textContent = call.name ? call.number || "" : "";
    statusEl.textContent = statusWord(call.status);
    root.dataset.status = call.status;
    if (call.status === "connected" && typeof call.elapsed_seconds === "number") {
      connectedAt = now() - call.elapsed_seconds * 1000;
      if (timer === null) timer = setInterval(renderTimer, 250);
    } else {
      connectedAt = null;
      stopTimer();
    }
    renderTimer();
    endButton.disabled = ended;
    root.hidden = false;
    document.body.classList.add("call--open");
  }

  endButton.addEventListener("click", () => {
    if (call === null || ended || options.demo) return;
    ended = true;
    endButton.disabled = true; // one tap is enough; the server clears the panel
    const tappedId = call.id;
    send({ type: "call_hangup", id: tappedId });
    // If the panel is somehow still up (the tap was dropped, the socket
    // was mid-reconnect), let her try again rather than leave a dead
    // button on the screen.
    setTimeout(() => {
      if (call !== null && call.id === tappedId) {
        ended = false;
        endButton.disabled = false;
      }
    }, RETRY_AFTER_MS);
  });

  if (options.demo && DEMO[options.demo]) show({ ...DEMO[options.demo] });

  return {
    onMessage(message) {
      if (options.demo) return;
      if (message.type !== "call") return;
      const next = message.call;
      if (next !== null && (typeof next !== "object" || typeof next.id !== "string")) return;
      show(next);
    },
    // For tests.
    _debug() {
      return { call, ended, open: !root.hidden };
    },
  };
}
