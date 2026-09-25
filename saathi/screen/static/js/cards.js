// Cards: the four primitives for resolving ambiguity by tap or voice —
// Choice, Confirm, Read-back, Holding — drawn beside the face, never in
// its place. The browser counterpart of screen/cards.py; the module the
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

const KINDS = new Set(["choice", "confirm", "readback", "holding"]);

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
