// The Ctrl+L settings panel (item C/G) — language and TTS backend
// selection, shared by both because they're the same kind of thing: a
// preference written through IdentityStore, effective on the next turn,
// with no restart. Explicitly for whoever sets the device up, not for
// her (SPEC/CLAUDE.md's "no status text under the face" rule is about
// *her* view; this is a setup surface that's normally invisible and
// only appears on a deliberate Ctrl+L, the one carve-out the brief
// asked for). Ctrl+L must never fall through to Chromium's own
// address-bar-focus binding, hence preventDefault() on every Ctrl+L
// keydown, whether that open or closes the panel.

export function createSettingsPanel(send) {
  let open = false;
  let settings = null; // last "settings" message received, or null before one arrives
  let lastError = null; // last failed set_preference's reason, cleared on the next attempt

  const root = document.createElement("div");
  root.id = "settings-panel";
  root.style.display = "none";
  document.body.appendChild(root);

  function render() {
    if (!open) {
      root.style.display = "none";
      root.replaceChildren();
      return;
    }
    root.style.display = "flex";
    root.replaceChildren();

    const title = document.createElement("h1");
    title.textContent = "Saathi settings";
    root.appendChild(title);

    if (settings === null) {
      const waiting = document.createElement("p");
      waiting.className = "settings-panel__note";
      waiting.textContent = "Connecting…";
      root.appendChild(waiting);
      return;
    }

    root.appendChild(renderLanguageSection());
    root.appendChild(renderBackendSection());
    root.appendChild(renderCaptionsSection());

    if (lastError !== null) {
      const error = document.createElement("p");
      error.className = "settings-panel__error";
      error.textContent = lastError;
      root.appendChild(error);
    }

    const hint = document.createElement("p");
    hint.className = "settings-panel__hint";
    hint.textContent = "Esc to close";
    root.appendChild(hint);
  }

  function renderLanguageSection() {
    const section = document.createElement("section");
    const heading = document.createElement("h2");
    heading.textContent = "Language";
    section.appendChild(heading);

    const list = document.createElement("div");
    list.className = "settings-panel__options";
    for (const language of settings.languages) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = language;
      button.className = "settings-panel__option";
      if (language === settings.current_language) {
        button.classList.add("settings-panel__option--selected");
      }
      button.addEventListener("click", () => {
        lastError = null;
        send({ type: "set_preference", key: "language", value: language });
      });
      list.appendChild(button);
    }
    section.appendChild(list);
    return section;
  }

  function renderBackendSection() {
    const section = document.createElement("section");
    const heading = document.createElement("h2");
    heading.textContent = "Voice (TTS backend)";
    section.appendChild(heading);

    const list = document.createElement("div");
    list.className = "settings-panel__options";
    for (const backend of settings.backends) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "settings-panel__option";
      if (!backend.available) {
        button.classList.add("settings-panel__option--unavailable");
        button.disabled = true;
      }
      if (backend.id === settings.current_backend) {
        button.classList.add("settings-panel__option--selected");
      }
      const cost =
        backend.cost_per_million_chars_usd > 0
          ? `$${backend.cost_per_million_chars_usd.toFixed(2)} / 1M chars`
          : "$0 (local)";
      button.textContent = backend.available
        ? `${backend.display_name} — ${cost}`
        : `${backend.display_name} — unavailable (${backend.reason})`;
      button.addEventListener("click", () => {
        if (!backend.available) return;
        lastError = null;
        send({ type: "set_preference", key: "tts_backend", value: backend.id });
      });
      list.appendChild(button);
    }
    section.appendChild(list);
    return section;
  }

  function renderCaptionsSection() {
    // Captions (captions.js): what she was heard saying, and what Saathi
    // said back, in a strip along the bottom. For whoever is setting up
    // or demoing the device; off by default.
    const section = document.createElement("section");
    const heading = document.createElement("h2");
    heading.textContent = "Captions (what it hears and says)";
    section.appendChild(heading);
    const list = document.createElement("div");
    list.className = "settings-panel__options";
    for (const [value, label] of [["on", "Show captions"], ["off", "Hide captions"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "settings-panel__option";
      if ((settings.captions === true) === (value === "on")) {
        button.classList.add("settings-panel__option--selected");
      }
      button.textContent = label;
      button.addEventListener("click", () => {
        lastError = null;
        send({ type: "set_preference", key: "captions", value });
      });
      list.appendChild(button);
    }
    section.appendChild(list);
    return section;
  }

  window.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.code === "KeyL") {
      event.preventDefault(); // never let Chromium grab this for its own address bar
      open = !open;
      render();
      return;
    }
    if (event.code === "Escape" && open) {
      event.preventDefault();
      open = false;
      render();
    }
  });

  return {
    isOpen() {
      return open;
    },
    onMessage(message) {
      if (message.type === "settings") {
        settings = message;
        render();
      } else if (message.type === "preference_result" && !message.ok) {
        // A successful write is already reflected by the "settings"
        // broadcast server.py sends right after it (server.py's
        // websocket_handler) — nothing extra to show. A failed one
        // (e.g. PreferenceLocked — see identity/preferences.py) must
        // say so here, not leave the panel looking like it worked:
        // "unavailable and visibly so beats silently broken" applies
        // to a rejected preference write same as anywhere else.
        lastError = message.reason;
        render();
      }
    },
  };
}
