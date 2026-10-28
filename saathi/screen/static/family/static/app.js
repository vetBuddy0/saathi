// The family app: pair once, then ring her and be rung by her, free,
// over the internet (call/webrtc.py is the other side of every message
// here). The device always makes the WebRTC offer; this page only
// answers it -- one direction of the hard part, not two.
//
// Credentials: pairing returns a member id and key, kept in
// localStorage on this origin only. A push link can also carry a
// per-call token (#call=..&t=..): that lets a page on a *new* origin --
// a quick tunnel whose hostname changed -- answer that one call.
//
// The microphone is asked for on the tap (Answer / Call), before
// anything is sent: a call is never accepted and then found mute, and
// iOS only lets audio play after a tap.

const STORE_KEY = "saathi-family";
const $ = (id) => document.getElementById(id);
const screens = ["unpaired", "pair", "home", "ringing", "call"];

function load() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || "null");
  } catch (err) {
    return null;
  }
}
function save(value) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(value));
  } catch (err) {
    /* private mode: pairing lasts this session only */
  }
}

let creds = load();
const params = new URLSearchParams(location.hash.slice(1));
const pairToken = params.get("pair");
let linkCall = params.get("call") ? { id: params.get("call"), token: params.get("t") } : null;
if (location.hash) history.replaceState(null, "", location.pathname);

let ws = null;
let retry = 0;
let callId = null;
let pc = null;
let mic = null;
let pending = [];
let timer = null;
const herName = () => (creds && creds.calls_her) || "Mum";

function show(name) {
  for (const s of screens) $(`screen-${s}`).hidden = s !== name;
}

function home(status = "") {
  stopCall();
  if (!creds) return show(pairToken ? "pair" : "unpaired");
  $("home-title").textContent = `Kaki — ${herName()}`;
  $("call-her").textContent = `Call ${herName()}`;
  $("call-her").hidden = !ws || ws.readyState !== WebSocket.OPEN || !canCall;
  $("home-status").textContent = status;
  refreshAlerts();
  show("home");
}

// -- pairing -----------------------------------------------------------

$("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("pair-error").hidden = true;
  const response = await fetch("/family/api/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      token: pairToken,
      name: $("pair-name").value,
      relation: $("pair-relation").value,
      calls_her: $("pair-her").value || "Mum",
    }),
  }).catch(() => null);
  const data = response ? await response.json().catch(() => null) : null;
  if (!data || !data.ok) {
    $("pair-error").textContent = (data && data.error) || "Couldn't reach Kaki. Try again.";
    $("pair-error").hidden = false;
    return;
  }
  creds = {
    member: data.member_id,
    key: data.key,
    calls_her: data.calls_her,
    vapid: data.vapid_public_key,
  };
  save(creds);
  connect();
  home("Paired. Turn on call alerts so this phone rings.");
});

// -- notifications (the ring) ---------------------------------------------

const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
const standalone =
  window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

async function registration() {
  if (!("serviceWorker" in navigator)) return null;
  return navigator.serviceWorker.register("/family/sw.js", { scope: "/family/" });
}

function urlBase64ToBytes(text) {
  const padded = (text + "=".repeat((4 - (text.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

function refreshAlerts() {
  const supported = "Notification" in window && "PushManager" in window;
  $("ios-hint").hidden = !(isIos && !standalone);
  $("alerts").hidden = !supported || Notification.permission === "granted";
}

async function subscribe() {
  if (!creds || !("Notification" in window)) return;
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    home("Call alerts are off, so this phone can't ring. You can still call her.");
    return;
  }
  const reg = await registration();
  if (!reg || !creds.vapid) return;
  await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToBytes(creds.vapid),
    });
  }
  const response = await fetch("/family/api/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ member_id: creds.member, key: creds.key, subscription: sub.toJSON() }),
  }).catch(() => null);
  home(response && response.ok ? "Call alerts are on." : "Couldn't turn on call alerts. Try again.");
}

$("alerts-on").addEventListener("click", () => subscribe());

// -- the socket ----------------------------------------------------------

let canCall = false;

function connect() {
  if (!creds && !linkCall) return;
  if (ws && ws.readyState <= WebSocket.OPEN) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${scheme}://${location.host}/family/ws`);
  ws.addEventListener("open", () => {
    retry = 0;
    const hello = creds
      ? { type: "hello", member: creds.member, key: creds.key }
      : { type: "hello", call_id: linkCall.id, call_token: linkCall.token };
    ws.send(JSON.stringify(hello));
  });
  ws.addEventListener("message", (event) => onMessage(JSON.parse(event.data)));
  ws.addEventListener("close", (event) => {
    if (event.code === 4003) {
      if (creds && !linkCall) {
        creds = null;
        save(null);
      }
      return home();
    }
    if (callId) stopCall();
    const delay = Math.min(30000, 500 * 2 ** retry++);
    setTimeout(connect, delay);
  });
}

function sendWs(message) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}

function onMessage(m) {
  switch (m.type) {
    case "welcome":
      canCall = !!m.can_call;
      if (m.calls_her && creds) creds.calls_her = m.calls_her;
      if (!callId) home();
      break;
    case "ringing":
      callId = m.call_id;
      $("ringing-name").textContent = `${herName()} is calling`;
      show("ringing");
      navigator.vibrate && navigator.vibrate([600, 300, 600, 300, 600]);
      break;
    case "waiting":
      callId = m.call_id;
      inCall(`Ringing ${herName()}…`);
      break;
    case "start":
      startPeer(m);
      break;
    case "signal":
      onSignal(m.data);
      break;
    case "connected":
      $("call-status").textContent = "Connected";
      startTimer();
      break;
    case "ended":
      if (m.call_id && callId && m.call_id !== callId) break;
      home(endedText(m.reason));
      break;
    default:
      break;
  }
}

function endedText(reason) {
  const her = herName();
  return (
    {
      busy: `${her} is on another call.`,
      declined: `${her} didn't answer.`,
      "no answer": `${her} didn't answer.`,
      "hung up": "Call ended.",
      "could not connect": "The call couldn't connect. Try again in a moment.",
      "answered elsewhere": "Answered on another phone.",
    }[reason] || "Call ended."
  );
}

// -- the call ------------------------------------------------------------

async function getMic() {
  if (mic) return mic;
  mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    video: false,
  });
  // Unlock playback on this tap (iOS).
  $("remote").play().catch(() => {});
  return mic;
}

function inCall(status) {
  $("call-name").textContent = herName();
  $("call-status").textContent = status;
  $("call-timer").textContent = "";
  show("call");
}

$("call-her").addEventListener("click", async () => {
  try {
    await getMic();
  } catch (err) {
    return home("Kaki needs the microphone to call. Allow it in your browser settings.");
  }
  sendWs({ type: "call" });
  inCall(`Ringing ${herName()}…`);
});

$("answer").addEventListener("click", async () => {
  try {
    await getMic();
  } catch (err) {
    return home("Kaki needs the microphone to answer. Allow it in your browser settings.");
  }
  sendWs({ type: "accept", call_id: callId });
  inCall("Connecting…");
});

$("decline").addEventListener("click", () => {
  sendWs({ type: "decline", call_id: callId });
  home();
});

$("end").addEventListener("click", () => {
  sendWs({ type: "end", call_id: callId });
  home("Call ended.");
});

async function startPeer(m) {
  callId = m.call_id;
  inCall("Connecting…");
  try {
    await getMic();
  } catch (err) {
    sendWs({ type: "end", call_id: callId });
    return home("Kaki needs the microphone. Allow it in your browser settings.");
  }
  pc = new RTCPeerConnection({ iceServers: m.ice_servers || [] });
  mic.getTracks().forEach((t) => pc.addTrack(t, mic));
  pc.ontrack = (event) => {
    $("remote").srcObject = event.streams[0];
    $("remote").play().catch(() => {});
  };
  pc.onicecandidate = (event) => {
    if (event.candidate) {
      sendWs({ type: "signal", call_id: callId, data: { candidate: event.candidate.toJSON() } });
    }
  };
  const queued = pending;
  pending = [];
  for (const data of queued) await onSignal(data);
}

async function onSignal(data) {
  if (!data) return;
  if (!pc) {
    pending.push(data);
    return;
  }
  try {
    if (data.sdp && data.sdp.type === "offer") {
      await pc.setRemoteDescription(data.sdp);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      sendWs({
        type: "signal",
        call_id: callId,
        data: { sdp: { type: pc.localDescription.type, sdp: pc.localDescription.sdp } },
      });
    } else if (data.candidate) {
      if (pc.remoteDescription) await pc.addIceCandidate(data.candidate);
      else pending.push(data);
    }
  } catch (err) {
    console.error("saathi: signal failed", err);
  }
}

function startTimer() {
  const started = Date.now();
  clearInterval(timer);
  timer = setInterval(() => {
    const s = Math.floor((Date.now() - started) / 1000);
    $("call-timer").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);
}

function stopCall() {
  clearInterval(timer);
  timer = null;
  if (pc) pc.close();
  pc = null;
  pending = [];
  if (mic) mic.getTracks().forEach((t) => t.stop());
  mic = null;
  $("remote").srcObject = null;
  callId = null;
}

// The service worker tells an open page about a ring, or hands it a
// notification tap.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (event) => {
    const msg = event.data || {};
    if (msg.type === "open" && msg.url) {
      const p = new URLSearchParams(new URL(msg.url).hash.slice(1));
      if (!creds && p.get("call")) linkCall = { id: p.get("call"), token: p.get("t") };
      connect();
    }
  });
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") connect();
});

if (creds) registration().catch(() => {});
home();
connect();
