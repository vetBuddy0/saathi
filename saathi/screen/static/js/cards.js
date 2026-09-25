// Cards: the five primitives for resolving ambiguity by tap or voice —
// Choice, Confirm, Read-back, Holding, Entry — drawn beside the face,
// never in its place. Entry (2026-09-26, the user's ask) is the number
// on a dialpad and the name, both hers to correct before Save: a digit
// key edits the number here at once and sends the edit; the server
// re-issues the card with the same id and the new values (a voice
// edit arrives the same way). The name is changed by voice -- an
// on-screen keyboard's keys would be a third the size of these. The browser counterpart of screen/cards.py; the module the
// other streams' screens go through. Nothing here decides what to ask:
// a card arrives as a `card` message from the server (a tool showed it)
// and a tap goes back as `card_answer`. A spoken answer never touches
// this file — it goes through a tool to the same CardController.
//
// Plain HTML and CSS on purpose. Every component library is built for a
// sighted adult with a mouse at arm's length; this is a 75-year-old
// across a room. The rules this file keeps (each load-bearing, from the
// brief): three options at most, numbered so "the second one" works;
// touch targets at least 100px tall; nothing smaller than 32px; AAA
// contrast (tokens in style.css, checked by tests/test_cards_style.py);
// no hover states; nothing times out — there is no setTimeout in this
// file, and tests grep for one; one card at a time — a new card
// replaces the old, never stacks; a large way out on every card that
// can be answered ("Never mind" / "Okay"), never a small ✕; tap only —
// no sliders, drag or long-press. Holding is the one card with no
// button: letting go of the spacebar is its way out.
//
// Cards are content and interaction, not status. "Listening…" describes
// the device and is forbidden (SPEC.md, CLAUDE.md); "Which one?" with
// three numbered names addresses her and waits for her answer.

const KINDS = new Set(["choice", "confirm", "readback", "holding", "entry"]);

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "+"];

// The raw digits (and a leading +) of a shown, grouped number.
function rawOf(shown) {
  return String(shown).replace(/\s+/g, "");
}

// The display for an edited number: keep the grouping she was shown
// for every leading digit that is unchanged; digits added after that
// go in fours. Same function as regroup_number in screen/cards.py, so
// a tap here and the server's re-issue agree.
function regroup(shown, raw) {
  let kept = "";
  let i = 0;
  for (const ch of shown) {
    if (ch === " ") {
      kept += ch;
      continue;
    }
    if (i < raw.length && raw[i] === ch) {
      kept += ch;
      i += 1;
    } else {
      break;
    }
  }
  let text = kept.replace(/\s+$/, "");
  for (const ch of raw.slice(i)) {
    const last = text ? text.split(" ").pop() : "";
    if (text && last.replace(/^\+/, "").length >= 4) text += " ";
    text += ch;
  }
  return text;
}

const DEMO = {
  "cards-choice": {
    kind: "choice",
    id: "demo-choice",
    title: "Which Priya?",
    spoken: "Which Priya? One: Priya, your daughter. Two: Priya from church. Three: Priya Menon.",
    options: [
      { n: 1, label: "Priya, your daughter" },
      { n: 2, label: "Priya from church" },
      { n: 3, label: "Priya Menon" },
    ],
  },
  "cards-confirm": {
    kind: "confirm",
    id: "demo-confirm",
    title: "Call Priya, your daughter?",
    spoken: "Call Priya, your daughter?",
  },
  "cards-readback": {
    kind: "readback",
    id: "demo-readback",
    title: "Priya's number",
    spoken: "Priya's number: zero four one, two three four, five six seven eight",
    value: "041 234 5678",
  },
  "cards-holding": {
    kind: "holding",
    id: "demo-holding",
    title: "Keep holding to hang up",
    spoken: "Keep holding to hang up",
    progress: 0.6,
  },
  "cards-entry": {
    kind: "entry",
    id: "demo-entry",
    title: "Priya's number",
    spoken:
      "Priya's number plus six five, nine one two three, four five six seven, and the name, " +
      "Priya. Say yes to save, or change the number or the name.",
    number: "+65 9123 4567",
    name: "Priya",
  },
};

function button(className, text, onTap) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = className;
  el.textContent = text;
  el.addEventListener("click", onTap);
  return el;
}

export function createCards(send, options = {}) {
  const demo = options.demo || null;
  let current = null; // the card message currently shown, or null

  const root = document.createElement("div");
  root.id = "card-panel";
  root.hidden = true;
  document.body.appendChild(root);

  function answer(cardId, payload) {
    send({ type: "card_answer", id: cardId, answer: payload });
  }

  function renderChoice(card, into) {
    const list = document.createElement("div");
    list.className = "card__options";
    for (const option of card.options.slice(0, 3)) {
      const el = button("card__option", "", () => answer(card.id, { choice: option.n }));
      const number = document.createElement("span");
      number.className = "card__number";
      number.textContent = String(option.n);
      const label = document.createElement("span");
      label.className = "card__label";
      label.textContent = option.label;
      el.replaceChildren(number, label);
      list.appendChild(el);
    }
    into.appendChild(list);
    into.appendChild(button("card__dismiss", "Never mind", () => answer(card.id, { dismiss: true })));
  }

  function renderConfirm(card, into) {
    const row = document.createElement("div");
    row.className = "card__options card__options--row";
    row.appendChild(button("card__option card__option--yes", "Yes", () => answer(card.id, { yes: true })));
    row.appendChild(button("card__option card__option--no", "No", () => answer(card.id, { yes: false })));
    into.appendChild(row);
    into.appendChild(button("card__dismiss", "Never mind", () => answer(card.id, { dismiss: true })));
  }

  function renderReadback(card, into) {
    const value = document.createElement("div");
    value.className = "card__value";
    value.textContent = card.value == null ? "" : String(card.value);
    into.appendChild(value);
    if (card.confirm) {
      // A read-back that is a question ("is that right?"): Yes / No,
      // and still a way out. Same buttons and sizes as a Confirm card.
      const row = document.createElement("div");
      row.className = "card__options card__options--row";
      row.appendChild(button("card__option card__option--yes", "Yes", () => answer(card.id, { yes: true })));
      row.appendChild(button("card__option card__option--no", "No", () => answer(card.id, { yes: false })));
      into.appendChild(row);
      into.appendChild(button("card__dismiss", "Never mind", () => answer(card.id, { dismiss: true })));
      return;
    }
    into.appendChild(button("card__dismiss", "Okay", () => answer(card.id, { dismiss: true })));
  }

  function renderEntry(card, into) {
    let shown = String(card.number);
    let name = String(card.name);

    const number = document.createElement("div");
    number.className = "card__value card__value--entry";
    number.textContent = shown;
    into.appendChild(number);

    const body = document.createElement("div");
    body.className = "card__entry";

    const pad = document.createElement("div");
    pad.className = "card__dialpad";
    const edit = (raw) => {
      shown = regroup(shown, raw);
      number.textContent = shown;
      answer(card.id, { number: raw });
    };
    for (const key of KEYS) {
      pad.appendChild(
        button("card__key", key, () => {
          const raw = rawOf(shown);
          if (key === "⌫") {
            if (raw) edit(raw.slice(0, -1));
          } else if (key === "+") {
            if (!raw) edit("+");
          } else {
            edit(raw + key);
          }
        })
      );
    }
    body.appendChild(pad);

    const side = document.createElement("div");
    side.className = "card__entry-side";
    const nameRow = document.createElement("div");
    nameRow.className = "card__name";
    const nameLabel = document.createElement("span");
    nameLabel.className = "card__name-label";
    nameLabel.textContent = "Name: ";
    const nameText = document.createElement("span");
    nameText.className = "card__name-text";
    nameText.textContent = name;
    nameRow.replaceChildren(nameLabel, nameText);
    side.appendChild(nameRow);
    const hint = document.createElement("div");
    hint.className = "card__hint";
    hint.textContent = "Say the new name.";
    hint.hidden = true;
    side.appendChild(button("card__option card__option--rename", "Change name", () => {
      hint.hidden = false;
    }));
    side.appendChild(hint);
    side.appendChild(
      button("card__option card__option--yes card__option--save", "Save", () =>
        answer(card.id, { yes: true, number: rawOf(shown), name })
      )
    );
    side.appendChild(
      button("card__option card__option--no", "Try again", () => answer(card.id, { yes: false }))
    );
    side.appendChild(button("card__dismiss", "Never mind", () => answer(card.id, { dismiss: true })));
    body.appendChild(side);
    into.appendChild(body);

    // The server re-issued the same card with new values (a voice
    // edit, or the echo of a tap): update in place, keep the dialpad.
    into.update = (next) => {
      shown = String(next.number);
      name = String(next.name);
      number.textContent = shown;
      nameText.textContent = name;
      hint.hidden = true;
    };
  }

  function renderHolding(card, into) {
    const track = document.createElement("div");
    track.className = "card__progress";
    const fill = document.createElement("div");
    fill.className = "card__progress-fill";
    const progress = Math.max(0, Math.min(1, Number(card.progress) || 0));
    fill.style.width = `${Math.round(progress * 100)}%`;
    track.appendChild(fill);
    into.appendChild(track);
  }

  function render() {
    root.replaceChildren();
    if (current === null) {
      root.hidden = true;
      document.body.classList.remove("card--open");
      window.dispatchEvent(new Event("resize"));
      return;
    }
    const card = document.createElement("div");
    card.className = `card card--${current.kind}`;
    card.dataset.cardId = current.id;
    const title = document.createElement("div");
    title.className = "card__title";
    title.textContent = current.title || "";
    card.appendChild(title);
    switch (current.kind) {
      case "choice":
        renderChoice(current, card);
        break;
      case "confirm":
        renderConfirm(current, card);
        break;
      case "readback":
        renderReadback(current, card);
        break;
      case "holding":
        renderHolding(current, card);
        break;
      case "entry":
        renderEntry(current, card);
        break;
      default:
        break;
    }
    root.replaceChildren(card);
    root.hidden = false;
    document.body.classList.add("card--open");
    // The face's container may have changed size (eyes-face.js only
    // reflows on a window resize).
    window.dispatchEvent(new Event("resize"));
  }

  function onCard(message) {
    const card = message.card;
    if (card === null || card === undefined) {
      current = null;
      render();
      return;
    }
    if (typeof card !== "object" || !KINDS.has(card.kind) || typeof card.id !== "string") {
      return; // malformed: dropped, the current card stays
    }
    if (card.kind === "choice" && !Array.isArray(card.options)) return;
    if (card.kind === "entry" && (typeof card.number !== "string" || typeof card.name !== "string")) {
      return;
    }
    if (current !== null && current.id === card.id && current.kind === "entry" && card.kind === "entry") {
      // The same entry card with edited values: update in place, so
      // the dialpad she is tapping on isn't rebuilt under her finger.
      current = card;
      const node = root.querySelector(".card");
      if (node && typeof node.update === "function") {
        node.update(card);
        return;
      }
    }
    if (current !== null && current.id === card.id && current.kind === "holding") {
      // Same hold advancing: update the bar in place rather than
      // rebuilding the card twenty times a hold.
      current = card;
      const fill = root.querySelector(".card__progress-fill");
      if (fill) {
        const progress = Math.max(0, Math.min(1, Number(card.progress) || 0));
        fill.style.width = `${Math.round(progress * 100)}%`;
        return;
      }
    }
    current = card; // one at a time: whatever was up is replaced
    render();
  }

  if (demo && DEMO[demo]) {
    onCard({ type: "card", card: DEMO[demo] });
  }

  return {
    onMessage(message) {
      if (message.type === "card") onCard(message);
    },
    // For tests only.
    _current() {
      return current;
    },
  };
}
