// The media panel: YouTube search results and the player, beside the
// face. Never instead of it — the face is in every layout this file can
// produce (see media-policy.js's layoutClasses), small in a corner when
// the video fills the screen.
//
// This is the first instance of the ambient content layer SPEC.md
// describes ("on-screen text is for content — time, name, reminder —
// never status") and TODO.md notes was never built. Search results and
// the title of what is playing are content. "Searching…", "Playing…",
// "Paused" are status and are not drawn, here or anywhere (CLAUDE.md).
//
// The browser decides nothing: every message here was emitted by
// tools/media.py's controller after the core validated the intent and
// the tool ran. This file only draws what it's told and reports the
// things only the player can know (ended, error, and "I have just
// loaded and have no player" on connect) back over the socket.
//
// Playback and autoplay: browsers block unmuted autoplay until the page
// has had a user gesture. The spacebar keydown that starts every turn is
// that gesture — an activation-triggering input event, and Chrome's
// activation is sticky for the document once it has happened — so the
// `play` message that follows a press may start sound. Playback is
// therefore always started from the message handler, never from a
// timer, and the kiosk additionally passes
// --autoplay-policy=no-user-gesture-required. The iframe is created
// here with allow="autoplay" rather than left to the IFrame API's
// defaults so that delegation is explicit.
//
// The IFrame API script is loaded lazily on the first `play`, not at
// page load: the face must come up with no network at all. The iframe,
// once created, is never removed from the DOM — only hidden. Removing
// it reloads the embed from its original src on re-insertion (the first
// video, again) and orphans the YT.Player wrapper; found in review.
//
// Rejected: rendering the player through a plain <iframe src=...> with
// no API. That can't be told to pause, set a volume, or report that a
// video ended — and "quieter" and "carry on" are half the brief.

import {
  ALL_LAYOUT_CLASSES,
  effectiveVolume,
  layoutClasses,
} from "./media-policy.js";

const IFRAME_API_SRC = "https://www.youtube.com/iframe_api";
const EMBED_BASE = "https://www.youtube.com/embed/";
const PLAYER_STATE_ENDED = 0; // YT.PlayerState.ENDED

const DEMO_RESULTS = [
  { index: 1, label: "One", title: "The Moon Represents My Heart - Teresa Teng", video_id: "d1" },
  {
    index: 2,
    label: "Two",
    title: "鄧麗君傳唱金曲 01 你怎麽説 02 小城故事 03 但願人長久",
    video_id: "d2",
  },
  {
    index: 3,
    label: "Three",
    title: "推荐50多岁以上的人真正喜欢的歌曲 50首70、80、90年代…",
    video_id: "d3",
  },
];

function loadIframeApi() {
  if (window.YT && window.YT.Player) return Promise.resolve();
  if (!loadIframeApi.pending) {
    loadIframeApi.pending = new Promise((resolve) => {
      const previous = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        if (typeof previous === "function") previous();
        resolve();
      };
      const script = document.createElement("script");
      script.src = IFRAME_API_SRC;
      document.head.appendChild(script);
    });
  }
  return loadIframeApi.pending;
}

export function createMediaPanel(send, options = {}) {
  const demo = options.demo || null;

  let view = "none"; // "none" | "results" | "player"
  let results = [];
  let current = null; // { video_id, title, index } of what is/was playing
  let baseVolume = 70;
  let fullscreen = false;
  let coreState = "sleeping";

  let player = null; // YT.Player once the API has attached
  let playerReady = false;
  let attaching = false; // an iframe + wrapper is being set up
  let loadedVideoId = null; // what the iframe was created with
  let pendingVideoId = null; // a play asked for before the player was ready

  const root = document.createElement("div");
  root.id = "media-panel";
  document.body.appendChild(root);

  const resultsEl = document.createElement("div");
  resultsEl.className = "media-results";
  resultsEl.hidden = true;
  root.appendChild(resultsEl);

  const playerWrap = document.createElement("div");
  playerWrap.className = "media-player";
  playerWrap.hidden = true;
  const frameHolder = document.createElement("div");
  frameHolder.className = "media-player__frame";
  playerWrap.appendChild(frameHolder);
  const titleEl = document.createElement("div");
  titleEl.className = "media-title";
  playerWrap.appendChild(titleEl);
  root.appendChild(playerWrap);

  function applyLayout() {
    document.body.classList.remove(...ALL_LAYOUT_CLASSES);
    document.body.classList.add(...layoutClasses(view, fullscreen));
    // The eyes face sizes its canvas off #face-container only on a
    // window resize (eyes-face.js); the container just changed size.
    window.dispatchEvent(new Event("resize"));
  }

  function render() {
    resultsEl.hidden = view !== "results";
    playerWrap.hidden = view !== "player";
    if (view === "results") {
      resultsEl.replaceChildren();
      for (const result of results) {
        const line = document.createElement("div");
        line.className = "media-result";
        const number = document.createElement("span");
        number.className = "media-result__number";
        number.textContent = `${result.label || result.index}:`;
        const title = document.createElement("span");
        title.className = "media-result__title";
        title.textContent = result.title;
        line.append(number, " ", title);
        resultsEl.appendChild(line);
      }
    } else if (view === "player") {
      titleEl.textContent = current ? current.title : "";
    }
    applyLayout();
  }

  function applyVolume() {
    if (player && playerReady && typeof player.setVolume === "function") {
      player.setVolume(effectiveVolume(baseVolume, coreState));
    }
  }

  function embedUrl(videoId) {
    // No autoplay=1 here: the video must not start before the wrapper
    // can set its (possibly ducked) volume. onReady starts it.
    const params = new URLSearchParams({
      enablejsapi: "1",
      controls: "0",
      rel: "0",
      modestbranding: "1",
      playsinline: "1",
      origin: window.location.origin,
    });
    return `${EMBED_BASE}${encodeURIComponent(videoId)}?${params}`;
  }

  function onPlayerReady(event) {
    playerReady = true;
    attaching = false;
    applyVolume();
    const wanted = pendingVideoId;
    pendingVideoId = null;
    if (wanted !== null && wanted !== loadedVideoId) {
      loadedVideoId = wanted;
      event.target.loadVideoById(wanted); // loads and plays
    } else {
      event.target.playVideo();
    }
  }

  function onPlayerStateChange(event) {
    // Only a video she is watching ending means anything. stopVideo()
    // (on a new search, or "stop") can surface ENDED too, and that must
    // not wipe the results just drawn or tell the server something ended.
    if (event.data === PLAYER_STATE_ENDED && view === "player") {
      send({ type: "media_event", event: "ended" });
      view = "none";
      render();
    }
  }

  function onPlayerError() {
    if (view !== "player") return;
    send({ type: "media_event", event: "error", video_id: current ? current.video_id : null });
    view = "none";
    render();
  }

  async function startPlayback(videoId) {
    if (demo) return; // the demo draws a fake player region, no network
    if (player && playerReady) {
      loadedVideoId = videoId;
      player.loadVideoById(videoId);
      applyVolume();
      player.playVideo();
      return;
    }
    pendingVideoId = videoId;
    if (attaching) return; // onReady will pick up pendingVideoId
    attaching = true;
    const iframe = document.createElement("iframe");
    iframe.className = "media-player__iframe";
    iframe.src = embedUrl(videoId);
    iframe.allow = "autoplay; encrypted-media";
    iframe.setAttribute("allowfullscreen", "");
    iframe.setAttribute("title", "video");
    loadedVideoId = videoId;
    frameHolder.replaceChildren(iframe);
    await loadIframeApi();
    player = new window.YT.Player(iframe, {
      events: {
        onReady: onPlayerReady,
        onStateChange: onPlayerStateChange,
        onError: onPlayerError,
      },
    });
  }

  function onMedia(message) {
    switch (message.action) {
      case "results":
        results = Array.isArray(message.results) ? message.results.slice(0, 3) : [];
        view = results.length ? "results" : "none";
        render();
        if (player && playerReady) player.stopVideo();
        break;
      case "play":
        current = { video_id: message.video_id, title: message.title, index: message.index };
        if (typeof message.volume === "number") baseVolume = message.volume;
        if (typeof message.fullscreen === "boolean") fullscreen = message.fullscreen;
        view = "player";
        render();
        startPlayback(message.video_id);
        break;
      case "pause":
        if (player && playerReady) player.pauseVideo();
        break;
      case "resume":
        if (player && playerReady) player.playVideo();
        break;
      case "stop":
        view = "none";
        render();
        if (player && playerReady) player.stopVideo();
        break;
      case "volume":
        if (typeof message.level === "number") baseVolume = message.level;
        applyVolume();
        break;
      case "layout":
        fullscreen = message.mode === "fullscreen";
        render();
        break;
      default:
        break;
    }
  }

  if (demo === "media") {
    results = DEMO_RESULTS;
    view = "results";
    render();
  } else if (demo === "media-playing" || demo === "media-fullscreen") {
    current = DEMO_RESULTS[0];
    fullscreen = demo === "media-fullscreen";
    view = "player";
    const fake = document.createElement("div");
    fake.className = "media-player__iframe media-player__iframe--demo";
    frameHolder.replaceChildren(fake);
    render();
  }

  return {
    onMessage(message) {
      if (message.type === "state") {
        coreState = message.state;
        applyVolume();
      } else if (message.type === "media") {
        onMedia(message);
      }
    },
    onConnected() {
      // A freshly loaded page has no player, whatever the server's
      // controller still believes was playing (a kiosk restart, a
      // reload). Say so, once, so "carry on" starts it again rather
      // than being told it's already playing. A socket that merely
      // reconnected while a player exists says nothing: nothing changed.
      if (!player && !attaching) {
        send({ type: "media_event", event: "reset" });
      }
    },
    // For tests only: the state this module holds, read-only.
    _debug() {
      return { view, fullscreen, baseVolume, coreState, playerReady, loadedVideoId };
    },
  };
}
