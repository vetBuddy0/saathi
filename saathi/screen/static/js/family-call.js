// The device end of a family-app call (call/webrtc.py), and the Ctrl+P
// pairing screen (call/family_server.py's local routes).
//
// Why the face page is the WebRTC endpoint: Chromium already has
// WebRTC, opens the system default microphone and speaker (no device is
// ever named -- CLAUDE.md), and its echo canceller has the far end's
// voice as its reference because Chromium is what plays it. Python only
// carries offer/answer/ICE between this page and the family member's
// phone, and decides who may talk to whom.
//
// This page decides nothing about a call: it rings when told to, starts
// a peer connection when told to, and reports back (ready, connected,
// failed). Answering is her action on the card or the spacebar, both
// handled server-side; hanging up is the hold seam / the phone panel's
// End button. Nothing here answers on its own.
//
// Several screens can be connected at once (a dev browser beside the
// kiosk). Each has its own `peer` id; the first to report `ready` is the
// device end and the server tells any other to stand down.
//
// Ctrl+P (for whoever sets the device up, like Ctrl+L): a QR code with a
// one-time pairing link, the paired phones, and an Unpair button each.
// preventDefault keeps Chromium's print dialog away.

const RING_ON_MS = 1600;
const RING_OFF_MS = 2400;

function newPeerId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `p${Date.now()}${Math.random().toString(16).slice(2)}`;
}

const STYLE = `
#family-pairing { position: fixed; inset: 0; z-index: 50; display: flex;
  align-items: center; justify-content: center; background: rgba(20,12,8,.92);
  color: #fff3e6; font: 22px/1.4 system-ui, sans-serif; }
#family-pairing[hidden] { display: none; }
#family-pairing .fp-box { max-width: min(900px, 94vw); max-height: 94vh; overflow: auto;
  display: flex; flex-wrap: wrap; gap: 40px; align-items: flex-start; }
#family-pairing .fp-qr { flex: none; }
#family-pairing .fp-qr svg { display: block; width: min(360px, 70vmin); height: auto;
  border-radius: 16px; }
#family-pairing h1 { margin: 0 0 12px; font-size: 32px; }
#family-pairing .fp-note { color: #ffcf9e; font-size: 18px; }
#family-pairing .fp-url { font-size: 14px; word-break: break-all; opacity: .7; }
#family-pairing ul { padding-left: 20px; }
#family-pairing button { font: inherit; font-size: 16px; margin-left: 12px;
  padding: 4px 12px; border-radius: 8px; border: 0; background: #7a3b2e; color: #fff; }
`;

export function createFamilyCall(send) {
  const peer = newPeerId();
  let callId = null;
  let pc = null;
  let stream = null;
  let pendingCandidates = [];
  let remoteSet = false;
  let ring = null;
  const audio = document.createElement("audio");
  audio.autoplay = true;
  audio.hidden = true;
  document.body.appendChild(audio);

  // -- ringing (an incoming call: she hears it, the eyes are told
  // separately by the server's emotion seam) --------------------------
  function startRing() {
    if (ring) return;
    let ctx;
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (err) {
      console.warn("saathi: no AudioContext for the ring", err);
      return;
    }
    const gain = ctx.createGain();
    gain.gain.value = 0;
    gain.connect(ctx.destination);
    for (const freq of [440, 480]) {
      const osc = ctx.createOscillator();
      osc.frequency.value = freq;
      osc.connect(gain);
      osc.start();
    }
    let on = false;
    const toggle = () => {
      on = !on;
      gain.gain.setTargetAtTime(on ? 0.12 : 0, ctx.currentTime, 0.02);
      ring.timer = setTimeout(toggle, on ? RING_ON_MS : RING_OFF_MS);
    };
    ring = { ctx, timer: null };
    toggle();
  }

  function stopRing() {
    if (!ring) return;
    clearTimeout(ring.timer);
    ring.ctx.close().catch(() => {});
    ring = null;
  }

  // -- the peer connection ----------------------------------------------
  function report(action, extra = {}) {
    send({ type: "rtc", action, call_id: callId, peer, ...extra });
  }

  function teardown() {
    stopRing();
    if (pc) {
      pc.onicecandidate = null;
      pc.onconnectionstatechange = null;
      pc.ontrack = null;
      pc.close();
    }
    if (stream) stream.getTracks().forEach((t) => t.stop());
    audio.srcObject = null;
    pc = null;
    stream = null;
    pendingCandidates = [];
    remoteSet = false;
    callId = null;
  }

  async function begin(message) {
    teardown();
    callId = message.call_id;
    const thisCall = callId;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
    } catch (err) {
      console.error("saathi: microphone unavailable for the call", err);
      report("ready");
      report("failed", { reason: "microphone" });
      return;
    }
    if (callId !== thisCall) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    pc = new RTCPeerConnection({ iceServers: message.ice_servers || [] });
    stream.getTracks().forEach((track) => pc.addTrack(track, stream));
    pc.ontrack = (event) => {
      audio.srcObject = event.streams[0];
      audio.play().catch((err) => console.warn("saathi: call audio blocked", err));
    };
    pc.onicecandidate = (event) => {
      if (event.candidate) report("signal", { data: { candidate: event.candidate.toJSON() } });
    };
    pc.onconnectionstatechange = () => {
      if (!pc) return;
      if (pc.connectionState === "connected") report("connected");
      if (pc.connectionState === "failed") report("failed", { reason: "ice" });
    };
    report("ready");
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    report("signal", { data: { sdp: { type: pc.localDescription.type, sdp: pc.localDescription.sdp } } });
  }

  async function onSignal(data) {
    if (!pc || !data) return;
    try {
      if (data.sdp && data.sdp.type === "answer") {
        await pc.setRemoteDescription(data.sdp);
        remoteSet = true;
        for (const candidate of pendingCandidates) await pc.addIceCandidate(candidate);
        pendingCandidates = [];
      } else if (data.candidate) {
        if (remoteSet) await pc.addIceCandidate(data.candidate);
        else pendingCandidates.push(data.candidate);
      }
    } catch (err) {
      console.error("saathi: bad signal from the family app", err);
    }
  }

  // -- the pairing screen (Ctrl+P) ---------------------------------------
  const style = document.createElement("style");
  style.textContent = STYLE;
  document.head.appendChild(style);
  const overlay = document.createElement("div");
  overlay.id = "family-pairing";
  overlay.hidden = true;
  document.body.appendChild(overlay);

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return response.json();
  }

  async function renderPairing() {
    overlay.replaceChildren(el("p", "Making a pairing code…"));
    let data;
    try {
      data = await post("/family-local/pairing");
    } catch (err) {
      overlay.replaceChildren(el("p", "The family app isn't running on this device."));
      return;
    }
    const box = el("div", null, "fp-box");
    const qr = el("div", null, "fp-qr");
    if (data.ok) {
      // segno's SVG, generated on this device from our own URL.
      qr.innerHTML = data.qr_svg;
    }
    const side = el("div");
    side.appendChild(el("h1", "Pair a family phone"));
    if (data.ok) {
      side.appendChild(
        el("p", `Scan this with the phone's camera. The code works once, for ${Math.round(data.expires_in / 60)} minutes.`)
      );
      if (data.note) side.appendChild(el("p", data.note, "fp-note"));
      side.appendChild(el("p", data.url, "fp-url"));
    } else {
      side.appendChild(el("p", data.reason || "The family app isn't ready yet.", "fp-note"));
    }
    side.appendChild(el("h2", "Paired phones"));
    const list = el("ul");
    for (const member of data.members || []) {
      const item = el("li", member.label);
      const unpair = el("button", "Unpair");
      unpair.addEventListener("click", async () => {
        await post("/family-local/unpair", { member_id: member.id });
        renderPairing();
      });
      item.appendChild(unpair);
      list.appendChild(item);
    }
    if (!list.children.length) list.appendChild(el("li", "None yet"));
    side.appendChild(list);
    side.appendChild(el("p", "Esc to close", "fp-note"));
    box.append(qr, side);
    overlay.replaceChildren(box);
  }

  window.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.code === "KeyP") {
      event.preventDefault();
      overlay.hidden = !overlay.hidden;
      if (!overlay.hidden) renderPairing();
    } else if (event.code === "Escape" && !overlay.hidden) {
      overlay.hidden = true;
      overlay.replaceChildren();
    }
  });

  return {
    isOpen: () => !overlay.hidden,
    onMessage(message) {
      if (!message || message.type !== "rtc") return;
      if (message.to && message.to !== peer) return;
      switch (message.action) {
        case "ring":
          startRing();
          break;
        case "ring_stop":
          stopRing();
          break;
        case "start":
          stopRing();
          begin(message);
          break;
        case "signal":
          if (message.call_id === callId) onSignal(message.data);
          break;
        case "end":
          if (!message.call_id || message.call_id === callId || !callId) teardown();
          break;
        default:
          break;
      }
    },
  };
}
