// Captions — what speech-to-text heard, and what she said back.
//
// Why this exists: in the first live demo on the built-in mic, turns
// failed in ways nobody in the room could see — "perfect words" for
// "play a song", or a reply that promised a song and did nothing. The
// person demoing needs to see what the device heard to tell a bad mic
// from a bad model. Turned on from the Ctrl+L panel (the `captions`
// preference); off by default, and nothing is sent while it's off.
//
// These are content, not status. SPEC.md/CLAUDE.md forbid status text
// under the face ("Listening…") because a person doesn't display a
// state label; a caption is the words themselves, as on a television.
// It never says what the device is doing, only what was said. The
// option that lost was logging transcripts to the terminal only: the
// person demoing is looking at the screen, not a log.
//
// Placement: a strip along the bottom, over nothing — the face and any
// card keep their space. Shows the latest exchange (her line, then
// Saathi's); a new turn replaces it. Nothing times out.

export function createCaptions() {
  let enabled = false;
  const lines = { her: "", saathi: "" };

  const root = document.createElement("div");
  root.id = "captions";
  root.hidden = true;
  document.body.appendChild(root);

  function line(cls, label, text) {
    const el = document.createElement("div");
    el.className = `captions__line ${cls}`;
    const who = document.createElement("span");
    who.className = "captions__who";
    who.textContent = label;
    const said = document.createElement("span");
    said.className = "captions__text";
    said.textContent = text;
    el.replaceChildren(who, said);
    return el;
  }

  function render() {
    const show = enabled && (lines.her || lines.saathi);
    root.hidden = !show;
    if (!show) {
      root.replaceChildren();
      return;
    }
    const children = [];
    if (lines.her) children.push(line("captions__line--her", "Heard", lines.her));
    if (lines.saathi) children.push(line("captions__line--saathi", "Saathi", lines.saathi));
    root.replaceChildren(...children);
  }

  return {
    onMessage(message) {
      if (message.type === "settings") {
        enabled = message.captions === true;
        if (!enabled) {
          lines.her = "";
          lines.saathi = "";
        }
        render();
      } else if (message.type === "caption" && enabled) {
        if (message.who === "her") {
          // A new turn: her words start a fresh exchange.
          lines.her = String(message.text || "");
          lines.saathi = "";
        } else if (message.who === "saathi") {
          lines.saathi = String(message.text || "");
        }
        render();
      }
    },
  };
}
