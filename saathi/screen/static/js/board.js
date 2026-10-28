// The screen's own things to tap: a row of big buttons under the face
// (Messages, Reminders, Call, Add a phone), the side panel each opens,
// and the card that shows a family message or a reminder while Melody
// says it.
//
// Why (2026-10-28, owner): a family message appeared only as a caption,
// reminders and calls had no page, and pairing a phone needed Ctrl+P.
// "There should be clickable things on the screen. Like for the tabs."
// And when something appears, "she should move her eyes to look at it":
// every panel and card here asks the face to glance at it (face.js's
// optional lookAt).
//
// Content, never status (SPEC.md): names, messages, reminder times. No
// "Listening", no "Connecting". A panel opens beside the face, never
// over it. The face moves left like it does for a call (style.css).
//
// The browser decides nothing. Panels read the server's loopback-only
// GETs (screen/board.py). The one action, ringing someone, is a
// `screen_action` asking for the `call_contact` tool, which the server
// runs through the same permission check as her voice. If the server
// refuses, the reason is said here in plain words.
//
// The row of buttons is dim until a pointer or finger is near and full
// strength then, so a resting screen is still mostly face. Lost:
// hiding it entirely until a tap. She would have to know it exists.

const CARD_SECONDS = 60;
const REGLANCE_MS = 8000;
const REGLANCES = 3;

const TABS = [
  { id: "messages", label: "Messages", icon: "message" },
  { id: "reminders", label: "Reminders", icon: "bell" },
  { id: "family", label: "Call", icon: "phone" },
  { id: "pair", label: "Add a phone", icon: "plus" },
];

const TITLES = { messages: "Messages", reminders: "Reminders", family: "Call your family" };

const ICONS = {
  message:
    '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4A2.5 2.5 0 0 1 4 13.5z"/>',
  bell:
    '<path d="M12 3a5.5 5.5 0 0 0-5.5 5.5c0 4-1.8 5.6-2.5 6.5h16c-.7-.9-2.5-2.5-2.5-6.5A5.5 5.5 0 0 0 12 3z"/><path d="M9.5 18.5a2.5 2.5 0 0 0 5 0"/>',
  phone:
    '<path d="M6.6 3.8 9 3.3l1.7 4.2-2 1.5a11 11 0 0 0 6.3 6.3l1.5-2 4.2 1.7-.5 2.4a2 2 0 0 1-2 1.6A15.7 15.7 0 0 1 5 5.8a2 2 0 0 1 1.6-2z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
};

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];

function icon(name) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

// The calendar's cells: whole weeks, Monday first, from the week holding
// `todayIso` through `days` days after it. Each cell is {iso, day,
// month, past, inRange}. Pure, for tests.
export function calendarCells(todayIso, days) {
  const [y, m, d] = todayIso.split("-").map(Number);
  const today = new Date(y, m - 1, d);
  const start = new Date(today);
  start.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const last = new Date(today);
  last.setDate(today.getDate() + days - 1);
  const end = new Date(last);
  end.setDate(last.getDate() + (6 - ((last.getDay() + 6) % 7)));
  const cells = [];
  for (let at = new Date(start); at <= end; at.setDate(at.getDate() + 1)) {
    const iso = `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
    cells.push({
      iso,
      day: at.getDate(),
      month: at.getMonth(),
      past: at < today,
      inRange: at >= today && at <= last,
    });
  }
  return cells;
}

// "Today", "Tomorrow", "Thursday 30 October".
export function dayTitle(iso, todayIso) {
  const [y, m, d] = iso.split("-").map(Number);
  const at = new Date(y, m - 1, d);
  const [ty, tm, td] = todayIso.split("-").map(Number);
  const diff = Math.round((at - new Date(ty, tm - 1, td)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  const weekday = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday"][at.getDay()];
  return `${weekday} ${at.getDate()} ${MONTHS[at.getMonth()]}`;
}

// What to tell her when a tap on "Call" didn't ring anyone. The tool's
// own `note` is written for the model, not for her.
export function callRefusal(name, result) {
  const status = result && result.status;
  if (status === "busy") return "There's already a call going.";
  if (status === "denied" || status === "refused") return "Calling isn't allowed from here.";
  return `Couldn't call ${name} just now.`;
}

export function createBoard(send, { face, openPairing, fetchJson } = {}) {
  const get = fetchJson || (async (path) => (await fetch(path)).json());
  const glance = (node, ms = 1500) => {
    if (!face || typeof face.lookAt !== "function" || !node) return;
    const r = node.getBoundingClientRect();
    if (r.width > 0) face.lookAt(r.left + r.width / 2, r.top + Math.min(r.height / 2, 160), ms);
  };

  // -- the row of buttons ---------------------------------------------------
  const dock = el("nav");
  dock.id = "dock";
  const buttons = {};
  for (const tab of TABS) {
    const button = el("button", "dock__button");
    button.type = "button";
    button.dataset.tab = tab.id;
    button.innerHTML = `${icon(tab.icon)}<span class="dock__label"></span>`;
    button.querySelector(".dock__label").textContent = tab.label;
    button.addEventListener("click", () => {
      if (tab.id === "pair") {
        close();
        if (openPairing) openPairing();
      } else if (openTab === tab.id) {
        close();
      } else {
        open(tab.id);
      }
    });
    buttons[tab.id] = button;
    dock.appendChild(button);
  }
  document.body.appendChild(dock);

  // -- the side panel --------------------------------------------------------
  const panel = el("section");
  panel.id = "board-panel";
  panel.hidden = true;
  const header = el("header", "board__header");
  const title = el("h1", "board__title");
  const closeButton = el("button", "board__close");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Close");
  closeButton.innerHTML = icon("close");
  closeButton.addEventListener("click", () => close());
  header.append(title, closeButton);
  const body = el("div", "board__body");
  panel.append(header, body);
  document.body.appendChild(panel);

  let openTab = null;
  let selectedDay = null;
  let loadSeq = 0;

  function open(tab) {
    openTab = tab;
    selectedDay = null;
    panel.hidden = false;
    panel.dataset.tab = tab;
    document.body.classList.add("board--open");
    for (const [id, button] of Object.entries(buttons)) {
      button.classList.toggle("dock__button--on", id === tab);
    }
    title.textContent = TITLES[tab] || "";
    load(tab);
    // After the layout has moved the face, so it looks the right way.
    requestAnimationFrame(() => glance(panel));
  }

  function close() {
    openTab = null;
    panel.hidden = true;
    body.replaceChildren();
    document.body.classList.remove("board--open");
    for (const button of Object.values(buttons)) button.classList.remove("dock__button--on");
  }

  async function load(tab) {
    const seq = ++loadSeq;
    let data;
    try {
      data = await get(`/screen-local/board/${tab}`);
    } catch (err) {
      data = { ok: false, reason: "That isn't available right now." };
    }
    if (seq !== loadSeq || openTab !== tab) return; // closed or switched meanwhile
    if (!data || !data.ok) {
      body.replaceChildren(el("p", "board__empty", (data && data.reason) || "Nothing here."));
      return;
    }
    if (tab === "messages") renderMessages(data.items || []);
    else if (tab === "reminders") renderReminders(data);
    else if (tab === "family") renderFamily(data.members || []);
  }

  function renderMessages(items) {
    if (!items.length) {
      body.replaceChildren(el("p", "board__empty", "No messages yet."));
      return;
    }
    const list = el("ol", "messages");
    for (const item of items) {
      const row = el("li", `message ${item.from_her ? "message--hers" : "message--theirs"}`);
      row.appendChild(el("div", "message__who", item.from_her ? `You to ${item.who}` : item.who));
      row.appendChild(el("div", "message__text", item.text));
      row.appendChild(el("div", "message__when", item.at));
      list.appendChild(row);
    }
    body.replaceChildren(list);
  }

  function renderReminders(data) {
    const byDay = data.by_day || {};
    const today = data.today;
    if (!selectedDay) selectedDay = today;
    const cells = calendarCells(today, data.days || 42);

    const calendar = el("div", "calendar");
    const months = [...new Set(cells.filter((c) => c.inRange).map((c) => MONTHS[c.month]))];
    calendar.appendChild(el("div", "calendar__months", months.join(" – ")));
    const grid = el("div", "calendar__grid");
    for (const name of WEEKDAYS) grid.appendChild(el("div", "calendar__weekday", name));
    for (const cell of cells) {
      const count = (byDay[cell.iso] || []).length;
      const button = el("button", "calendar__day");
      button.type = "button";
      button.disabled = !cell.inRange;
      button.classList.toggle("calendar__day--past", cell.past);
      button.classList.toggle("calendar__day--today", cell.iso === today);
      button.classList.toggle("calendar__day--selected", cell.iso === selectedDay);
      button.appendChild(el("span", "calendar__date", String(cell.day)));
      if (count) {
        const dots = el("span", "calendar__dots");
        for (let i = 0; i < Math.min(count, 3); i += 1) dots.appendChild(el("i"));
        button.appendChild(dots);
      }
      button.addEventListener("click", () => {
        selectedDay = cell.iso;
        renderReminders(data);
      });
      grid.appendChild(button);
    }
    calendar.appendChild(grid);

    const day = el("div", "day");
    day.appendChild(el("h2", "day__title", dayTitle(selectedDay, today)));
    const items = byDay[selectedDay] || [];
    if (!items.length) {
      day.appendChild(el("p", "board__empty", "No reminders."));
    } else {
      const list = el("ul", "day__list");
      for (const item of items) {
        const row = el("li", "reminder");
        row.appendChild(el("span", "reminder__at", item.at));
        row.appendChild(el("span", "reminder__text", item.text));
        if (item.repeat === "daily") row.appendChild(el("span", "reminder__repeat", "Every day"));
        if (item.repeat === "weekly") row.appendChild(el("span", "reminder__repeat", "Every week"));
        list.appendChild(row);
      }
      day.appendChild(list);
    }
    day.appendChild(el("p", "board__hint", "To add or change one, just ask Melody."));
    body.replaceChildren(calendar, day);
  }

  function renderFamily(members) {
    if (!members.length) {
      const empty = el("div", "board__empty");
      empty.appendChild(el("p", null, "No family phones yet."));
      const add = el("button", "board__action", "Add a phone");
      add.type = "button";
      add.addEventListener("click", () => {
        close();
        if (openPairing) openPairing();
      });
      empty.appendChild(add);
      body.replaceChildren(empty);
      return;
    }
    const list = el("div", "people");
    const said = el("p", "board__note");
    for (const member of members) {
      const button = el("button", "person");
      button.type = "button";
      button.innerHTML = `${icon("phone")}<span class="person__name"></span><span class="person__relation"></span>`;
      button.querySelector(".person__name").textContent = member.name;
      button.querySelector(".person__relation").textContent = member.relation || "";
      button.addEventListener("click", () => {
        said.textContent = "";
        pendingCall = { name: member.name, said };
        send({ type: "screen_action", tool: "call_contact", args: { contact: member.name } });
      });
      list.appendChild(button);
    }
    body.replaceChildren(list, said);
  }

  let pendingCall = null;

  // -- the card: what Melody is reading to her -------------------------------
  const card = el("aside");
  card.id = "shown-card";
  card.hidden = true;
  document.body.appendChild(card);
  let cardTimer = null;
  let reglanceTimer = null;

  function showCard(message) {
    const kind = message.kind === "reminder" ? "reminder" : "message";
    card.dataset.kind = kind;
    const head = el("div", "shown__head");
    head.innerHTML = icon(kind === "reminder" ? "bell" : "message");
    head.appendChild(
      el("span", "shown__from", kind === "reminder" ? "Reminder" : String(message.from || ""))
    );
    const text = el("p", "shown__text", String(message.text || ""));
    const when = el("div", "shown__when", String(message.at || ""));
    const ok = el("button", "shown__ok", "OK");
    ok.type = "button";
    ok.addEventListener("click", hideCard);
    card.replaceChildren(head, text, when, ok);
    // Fresh each time, so the arrival animation plays again.
    card.classList.remove("shown--in");
    card.hidden = false;
    void card.offsetWidth;
    card.classList.add("shown--in");
    document.body.classList.add("shown--open");

    if (face && typeof face.react === "function") face.react("bounce");
    requestAnimationFrame(() => glance(card, 1800));
    // While it's up she may look away and back; so does Melody, now and then.
    let left = REGLANCES;
    clearInterval(reglanceTimer);
    reglanceTimer = setInterval(() => {
      left -= 1;
      if (left < 0 || card.hidden) {
        clearInterval(reglanceTimer);
        return;
      }
      glance(card, 1100);
    }, REGLANCE_MS);
    clearTimeout(cardTimer);
    cardTimer = setTimeout(hideCard, CARD_SECONDS * 1000);
    if (openTab === "messages" && kind === "message") load("messages");
  }

  function hideCard() {
    clearTimeout(cardTimer);
    clearInterval(reglanceTimer);
    card.hidden = true;
    card.replaceChildren();
    document.body.classList.remove("shown--open");
  }

  return {
    isOpen: () => openTab !== null,
    open,
    close,
    onMessage(message) {
      if (!message) return;
      if (message.type === "shown") {
        showCard(message);
      } else if (message.type === "screen_action_result" && pendingCall) {
        const { name, said } = pendingCall;
        pendingCall = null;
        const status = message.result && message.result.status;
        if (status === "calling") {
          close(); // the phone panel takes the right side from here
        } else {
          said.textContent = callRefusal(name, message.result);
        }
      } else if (message.type === "call" && message.call) {
        close(); // a call coming in gets the right side
      }
    },
  };
}
