// The clock: when she asks the time, sets a timer or starts a stopwatch,
// the right of the screen shows the time and date in big figures, each
// timer counting down, and the stopwatch counting up. The face moves to
// the left, as for a call; it is never hidden.
//
// Why: the owner asked (2026-10-29) for "a small clock Saathi can pull
// up for timers, stopwatch etc". A timer she can only ask about ("how
// long is left?") is a timer she has to keep asking about.
//
// The browser decides nothing. It draws the server's `clock` message
// (tools/clock.py) and counts the seconds itself, so nothing ticks over
// the socket; the close button and a finished timer's Stop only send
// `{type: "clock_event", ...}` back, and the server changes the clock.
// The server's own clock is the reference (`now_ms`), so a laptop
// browser whose clock is off still counts down to the right moment.
//
// A finished timer chimes here, and Saathi says so out loud
// (screen/server.py) once she is free. The chime repeats every
// CHIME_AGAIN_MS while it is shown and not stopped, like a kitchen
// timer, but never more often. Behind a card, the phone or the
// media panel the clock is hidden (those are what she is answering or
// watching); the spoken "done" still reaches her. During a call it is
// silent, chime and words both, and a small card says it instead.
//
// The time and date are content she asked for, beside the face like
// the phone's screen -- not a status label under it (CLAUDE.md).

import { formatDuration } from "./call-panel.js";

const TICK_MS = 250;
const CHIME_AGAIN_MS = 15000;

// "Tea", or "5 minutes" for a timer she did not name.
export function timerName(timer) {
  if (timer.label) return timer.label.charAt(0).toUpperCase() + timer.label.slice(1);
  const total = Math.max(0, Math.round(Number(timer.seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const parts = [];
  if (h) parts.push(`${h} hour${h === 1 ? "" : "s"}`);
  if (m) parts.push(`${m} minute${m === 1 ? "" : "s"}`);
  if (s) parts.push(`${s} second${s === 1 ? "" : "s"}`);
  return parts.join(" ") || "Timer";
}

// What's left, rounded up: a timer at 0.2 s left still shows 0:01.
export function remaining(endsAtMs, nowMs) {
  return formatDuration(Math.ceil(Math.max(0, endsAtMs - nowMs) / 1000));
}

function playChime() {
  let ctx;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
  } catch (err) {
    console.warn("saathi: no AudioContext for the timer chime", err);
    return;
  }
  const start = ctx.currentTime + 0.05;
  // Three soft two-note chimes, about a second and a half.
  for (let i = 0; i < 3; i += 1) {
    for (const [offset, freq] of [[0, 880], [0.18, 1175]]) {
      const at = start + i * 0.5 + offset;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(0.18, at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, at + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start(at);
      osc.stop(at + 0.32);
    }
  }
  setTimeout(() => ctx.close().catch(() => {}), 2500);
}

export function createClockPanel(send, options = {}) {
  const localNow = options.now || (() => Date.now());
  const chime = options.chime || playChime;

  const root = document.createElement("div");
  root.id = "clock-panel";
  root.hidden = true;
  root.innerHTML = `
    <button type="button" class="clock-panel__close" aria-label="Close the clock">&times;</button>
    <div class="clock-panel__time"></div>
    <div class="clock-panel__date"></div>
    <ul class="clock-panel__timers"></ul>
    <div class="clock-panel__stopwatch" hidden>
      <div class="clock-panel__label">Stopwatch</div>
      <div class="clock-panel__count"></div>
    </div>
  `;
  document.body.appendChild(root);
  const timeEl = root.querySelector(".clock-panel__time");
  const dateEl = root.querySelector(".clock-panel__date");
  const timersEl = root.querySelector(".clock-panel__timers");
  const stopwatchEl = root.querySelector(".clock-panel__stopwatch");
  const stopwatchCount = stopwatchEl.querySelector(".clock-panel__count");

  let state = null;
  let offset = 0; // server clock minus this page's
  let ticker = null;
  const chimedAt = new Map(); // timer id -> when it last chimed (local ms)
  const stopped = new Set(); // ids whose Stop was tapped

  const now = () => localNow() + offset;

  function render() {
    if (state === null) return;
    const at = new Date(now());
    timeEl.textContent = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    dateEl.textContent = at.toLocaleDateString([], {
      weekday: "long",
      day: "numeric",
      month: "long",
    });
    for (const row of timersEl.children) {
      const timer = state.timers.find((t) => t.id === row.dataset.id);
      if (!timer) continue;
      const done = timer.done || timer.ends_at_ms <= now();
      row.classList.toggle("clock-panel__timer--done", done);
      row.querySelector(".clock-panel__count").textContent = done
        ? "Time's up"
        : remaining(timer.ends_at_ms, now());
      row.querySelector(".clock-panel__stop").hidden = !timer.done || stopped.has(timer.id);
    }
    const watch = state.stopwatch;
    stopwatchEl.hidden = !watch;
    if (watch) {
      const ms = watch.elapsed_ms + (watch.running ? Math.max(0, now() - watch.since_ms) : 0);
      stopwatchCount.textContent = formatDuration(ms / 1000);
      stopwatchEl.dataset.running = watch.running ? "yes" : "no";
    }
  }

  function ring() {
    // Not during a call: the other person would hear it too. The phone
    // panel's compact "Timer done" card is the signal then. If the timer
    // is still shown when the call ends (the server keeps a finished one
    // a minute) and nobody stopped it, it chimes then.
    if (document.body.classList.contains("call--open")) return;
    const t = localNow();
    let due = false;
    for (const timer of state.timers) {
      if (!timer.done || stopped.has(timer.id)) continue;
      const last = chimedAt.get(timer.id);
      if (last === undefined || t - last >= CHIME_AGAIN_MS) {
        chimedAt.set(timer.id, t);
        due = true;
      }
    }
    if (due) chime();
  }

  function tick() {
    render();
    if (state !== null) ring();
  }

  function drawTimers() {
    timersEl.textContent = "";
    for (const timer of state.timers) {
      const row = document.createElement("li");
      row.className = "clock-panel__timer";
      row.dataset.id = timer.id;
      row.innerHTML = `
        <div class="clock-panel__label"></div>
        <div class="clock-panel__count"></div>
        <button type="button" class="clock-panel__stop" hidden>Stop</button>`;
      row.querySelector(".clock-panel__label").textContent = timerName(timer);
      row.querySelector(".clock-panel__stop").addEventListener("click", () => {
        if (stopped.has(timer.id)) return; // one tap is enough
        stopped.add(timer.id);
        render();
        send({ type: "clock_event", action: "dismiss", id: timer.id });
      });
      timersEl.appendChild(row);
    }
  }

  function show(next) {
    const ids = new Set(next.timers.map((t) => t.id));
    for (const id of [...chimedAt.keys()]) if (!ids.has(id)) chimedAt.delete(id);
    for (const id of [...stopped]) if (!ids.has(id)) stopped.delete(id);
    if (typeof next.now_ms === "number") offset = next.now_ms - localNow();
    state = next;
    drawTimers();
    if (!next.visible) {
      root.hidden = true;
      document.body.classList.remove("clock--open");
      if (ticker !== null) clearInterval(ticker);
      ticker = null;
      return;
    }
    root.hidden = false;
    document.body.classList.add("clock--open");
    if (ticker === null) ticker = setInterval(tick, TICK_MS);
    tick();
  }

  root.querySelector(".clock-panel__close").addEventListener("click", () => {
    send({ type: "clock_event", action: "hide" });
  });

  return {
    onMessage(message) {
      if (message.type !== "clock") return;
      if (typeof message.visible !== "boolean" || !Array.isArray(message.timers)) return;
      show(message);
    },
    // For tests.
    _debug() {
      return { open: !root.hidden, offset };
    },
  };
}
