// Twilio calls from this page: the device's end of "call my son" when the
// engine runs inside the Android app (call/web_phone.py).
//
// Why here: the Twilio path built for the laptop needs a public tunnel
// and the laptop's echo-cancelled audio, neither of which a tablet has.
// Twilio's Voice JavaScript SDK needs only a page with a microphone, and
// this page is one -- served from http://127.0.0.1, a secure context --
// with WebRTC's own echo cancelling. The SDK is vendored
// (static/vendor/twilio-voice-2.18.5.min.js, Apache-2.0) so a demo never
// waits on a CDN, and loaded only when the engine hands out a token.
//
// The page decides nothing. The engine chooses who to call, owns the
// call's state, draws the phone panel (call-panel.js) and holds the one
// hang-up path; this module places the call it is told to place, ends
// the one it is told to end, and reports what its Twilio.Call did:
//
//   in   {type: "phone", via: "twilio", action: "dial", id, number}
//   in   {type: "phone", via: "twilio", action: "hangup", id}
//   out  {type: "phone_status", via: "twilio", can_call}
//   out  {type: "phone_event", id, event: ringing|connected|ended|failed, detail?}
//
// A dial waits until core.py's state is free again (idle, attentive,
// sleeping) so "Calling Udhi." is heard before the call takes the audio
// -- the same rule as the Android shell's PhoneCalls.kt -- and rings
// anyway after MAX_WAIT_MS.

const SDK_URL = "/static/vendor/twilio-voice-2.18.5.min.js";
const TOKEN_URL = "/phone/token";
const AFTER_SPEECH_MS = 400;
const MAX_WAIT_MS = 10000;
const FREE_STATES = new Set(["idle", "attentive", "sleeping"]);

function loadSdk() {
  if (window.Twilio && window.Twilio.Device) return Promise.resolve(window.Twilio);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SDK_URL;
    script.onload = () => resolve(window.Twilio);
    script.onerror = () => reject(new Error("the Twilio SDK did not load"));
    document.head.appendChild(script);
  });
}

async function fetchToken() {
  const response = await fetch(TOKEN_URL, { cache: "no-store" });
  if (!response.ok) return null; // 404: this engine has no Twilio route
  const body = await response.json();
  return typeof body.token === "string" ? body.token : null;
}

export function createTwilioPhone(send) {
  let device = null;
  let starting = null;
  let state = "idle";
  let pending = null; // {id, number} waiting for her sentence to end
  let timer = null;
  let call = null; // {id, twilioCall}

  function report(id, event, detail) {
    const payload = { type: "phone_event", id, event };
    if (detail) payload.detail = String(detail).slice(0, 200);
    send(payload);
  }

  async function start() {
    const token = await fetchToken();
    if (!token) return false;
    const Twilio = await loadSdk();
    device = new Twilio.Device(token, {
      codecPreferences: ["opus", "pcmu"],
      closeProtection: false,
      logLevel: "warn",
    });
    device.on("tokenWillExpire", async () => {
      try {
        const fresh = await fetchToken();
        if (fresh) device.updateToken(fresh);
      } catch (error) {
        console.error("saathi: twilio token refresh failed", error);
      }
    });
    device.on("error", (error) => console.error("saathi: twilio device error", error));
    return true;
  }

  // Every socket open: (re)learn whether calls can go this way and say so.
  async function onConnected() {
    try {
      if (!device) {
        starting = starting || start();
        if (!(await starting)) {
          starting = null;
          return;
        }
      }
      send({ type: "phone_status", via: "twilio", can_call: true });
    } catch (error) {
      starting = null;
      console.error("saathi: twilio calling unavailable", error);
    }
  }

  function schedule(delayMs) {
    clearTimeout(timer);
    timer = setTimeout(placePending, delayMs);
  }

  async function placePending() {
    timer = null;
    const wanted = pending;
    pending = null;
    if (!wanted || !device) return;
    try {
      const twilioCall = await device.connect({ params: { To: wanted.number } });
      call = { id: wanted.id, twilioCall };
      const finish = (event, detail) => {
        if (call && call.id === wanted.id) call = null;
        report(wanted.id, event, detail);
      };
      twilioCall.on("ringing", () => report(wanted.id, "ringing"));
      twilioCall.on("accept", () => report(wanted.id, "connected"));
      twilioCall.on("disconnect", () => finish("ended"));
      twilioCall.on("cancel", () => finish("ended"));
      twilioCall.on("reject", () => finish("ended"));
      twilioCall.on("error", (error) => finish("failed", error && error.message));
    } catch (error) {
      console.error("saathi: twilio call could not start", error);
      report(wanted.id, "failed", error && error.message);
    }
  }

  function onMessage(message) {
    if (message.type === "state") {
      state = message.state;
      if (pending && FREE_STATES.has(state)) schedule(AFTER_SPEECH_MS);
      return;
    }
    if (message.type !== "phone" || message.via !== "twilio") return;
    if (message.action === "dial" && message.id && message.number) {
      pending = { id: message.id, number: message.number };
      schedule(FREE_STATES.has(state) ? AFTER_SPEECH_MS : MAX_WAIT_MS);
    } else if (message.action === "hangup") {
      if (pending && pending.id === message.id) {
        pending = null;
        clearTimeout(timer);
      }
      if (call && call.id === message.id) {
        const { twilioCall } = call;
        call = null;
        twilioCall.disconnect();
      }
    }
  }

  return { onMessage, onConnected };
}
