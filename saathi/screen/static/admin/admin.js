// The developer console's page (screen/admin.py has the why). Draws each
// tab from /admin/api/* and sends edits back; decides nothing itself.
// Every string from the store goes in as text, never as HTML.
//
// Keys: on the tablet the shell owns them (encrypted prefs) and offers
// `window.SaathiShell` (ConsoleActivity.kt) -- status by name and a save
// that restarts the engine. In a laptop browser there is no bridge, so
// the tab shows status only and says where the keys live.

const shell = window.SaathiShell || null;

// -- tiny helpers -----------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const $ = (id) => document.getElementById(id);

let toastTimer = null;
function toast(message, bad = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = bad ? "toast toast--bad" : "toast";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 2500);
}

async function api(path, options = {}) {
  const init = { cache: "no-store", ...options };
  if (options.json !== undefined) {
    init.body = JSON.stringify(options.json);
    init.headers = { "Content-Type": "application/json" };
    init.method = options.method || "POST";
  }
  const response = await fetch(`/admin/api/${path}`, init);
  if (!response.ok) {
    const text = (await response.text()) || response.statusText;
    throw new Error(text);
  }
  return response.json();
}

function when(ts) {
  if (!ts) return "";
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleString();
}

function empty(text) {
  return h("li", { class: "empty" }, text);
}

async function act(promise, done) {
  try {
    await promise;
    if (done) toast(done);
    await loadAll();
  } catch (error) {
    toast(error.message, true);
  }
}

// -- tabs -------------------------------------------------------------------

function show(tab) {
  for (const button of document.querySelectorAll(".tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.hidden = panel.id !== `tab-${tab}`;
  }
  history.replaceState(null, "", `#${tab}`);
}

document.querySelector(".tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("button")?.dataset.tab;
  if (tab) show(tab);
});
document.addEventListener("click", (event) => {
  const goto = event.target.closest("[data-goto]")?.dataset.goto;
  if (goto) { event.preventDefault(); show(goto); }
});

// -- How it works -------------------------------------------------------------

function drawOverview(data) {
  const c = data.counts;
  $("stats").replaceChildren(
    ...[
      [c.rules, "learned facts (rules)"],
      [c.episodes, "episodes remembered"],
      [c.people, "contacts"],
      [c.turns, "turns so far"],
    ].map(([n, label]) => h("div", { class: "card" }, h("b", {}, n), h("span", {}, label))),
  );

  const engine = data.engine || {};
  const steps = [
    ["Listen", "Her voice is recorded while she holds the button; silence is dropped before anything is sent."],
    ["Hear", `Speech to text: ${engine.stt_model || "–"} (${engine.provider || "no AI key"}).`],
    ["Context", "Persona + rules + 5 episodes (compiled after the last turn) + this conversation's summary and window."],
    ["Think", `Reply: ${engine.llm_model || "–"}. It may call one tool: ${(engine.tools || []).join(", ") || "none"}.`],
    ["Act", "A tool call is checked against its permission by the core, then run; the model only asks."],
    ["Speak", `Voice: ${engine.voice || "–"}, one sentence at a time so she hears the start sooner.`],
  ];
  $("flow").replaceChildren(...steps.map(([title, text]) => h("li", {}, h("b", {}, title), h("span", {}, text))));

  const run = data.runtime || {};
  const facts = [
    ["AI provider", engine.provider ? `${engine.provider} · ${engine.llm_model}` : "not set (no AI key)"],
    ["Hearing", engine.stt_model || "–"],
    ["Voice", engine.voice || "–"],
    ["Calls go via", run.calls || "–"],
    ["Cloud backup", run.cloud || "off"],
    ["Audio", run.audio === "remote" ? "this tablet's mic and speaker" : run.audio || "–"],
    ["Personality", data.persona_source === "edited" ? "edited in this console" : "shipped default"],
    ["Data folder", run.data_dir || "–"],
  ];
  $("runtime").replaceChildren(...facts.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  const notes = run.notes || [];
  $("notes").replaceChildren(...(notes.length ? notes.map((n) => h("li", {}, n)) : [h("li", {}, "none")]));
}

// -- Memory -------------------------------------------------------------------

function ruleRow(rule) {
  const text = h("div", { class: "row__text" }, rule.text);
  const actions = h("div", { class: "row__actions" });
  const row = h("li", { class: "row" }, text, actions,
    h("div", { class: "row__meta" },
      h("span", {}, `memory ${rule.id}`),
      h("span", {}, `learned ${when(rule.learned_at)}`),
      rule.confidence != null ? h("span", {}, `confidence ${rule.confidence}`) : null,
      rule.source ? h("span", { title: rule.source }, `from: “${rule.source.slice(0, 90)}${rule.source.length > 90 ? "…" : ""}”`) : null,
    ));
  if (!rule.active) return row;

  actions.append(
    h("button", { class: "btn btn--ghost btn--small", type: "button", onclick: () => {
      const area = h("textarea", { rows: 3, maxlength: 2000 });
      area.value = rule.text;
      const save = h("button", { class: "btn btn--small", type: "button", onclick: () =>
        act(api(`rules/${rule.id}`, { json: { text: area.value } }), "Rule updated (old one retired)") }, "Save");
      const cancel = h("button", { class: "btn btn--ghost btn--small", type: "button", onclick: () => loadAll() }, "Cancel");
      text.replaceWith(area);
      actions.replaceChildren(save, cancel);
      area.focus();
    } }, "Edit"),
    h("button", { class: "btn btn--danger btn--small", type: "button", onclick: () => {
      if (confirm(`Retire memory ${rule.id}? It stops being sent; it stays in history.`)) {
        act(api(`rules/${rule.id}/retire`, { json: {} }), "Rule retired");
      }
    } }, "Retire"),
  );
  return row;
}

function drawMemory(data) {
  const st = data.short_term;
  const box = $("short-term");
  if (!st) {
    box.replaceChildren(h("p", { class: "empty" }, "No live session (no AI key), so no conversation."));
  } else {
    box.replaceChildren(
      h("div", { class: "summary" }, h("b", {}, "Summary: "), st.summary || h("span", { class: "muted" }, "nothing folded yet")),
      st.window.length
        ? h("div", { class: "chat" }, st.window.flatMap((e) => [
            h("p", { class: "her" }, h("b", {}, "Her"), e.her),
            h("p", { class: "saathi" }, h("b", {}, "Saathi"), e.saathi),
          ]))
        : h("p", { class: "empty" }, "No exchanges since the engine started."),
    );
  }

  const active = data.rules.filter((r) => r.active);
  const retired = data.rules.filter((r) => !r.active);
  $("rules").replaceChildren(...(active.length ? active.map(ruleRow) : [empty("No rules yet. Add one above: automatic learning from conversations is not switched on yet (Under the hood).")]));
  $("rules-retired").replaceChildren(...(retired.length ? retired.map(ruleRow) : [empty("None.")]));
  $("retired-count").textContent = `(${retired.length})`;

  $("episodes").replaceChildren(...(data.episodes.length ? data.episodes.map((e) =>
    h("li", { class: "row" },
      h("div", { class: "row__text" }, e.text),
      e.sent ? h("span", { class: "pill pill--sent" }, "sent") : h("span"),
      h("div", { class: "row__meta" },
        h("span", {}, `#${e.id}`),
        h("span", {}, when(e.ts)),
        h("span", {}, `importance ${e.importance ?? "–"}`),
        h("span", {}, e.embedded ? "has meaning vector" : "no vector yet"),
      ))) : [empty("No episodes yet.")]));

  const prefs = Object.entries(data.preferences);
  $("preferences").replaceChildren(...(prefs.length
    ? prefs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "")])
    : [h("dt", {}, "none"), h("dd", {}, "defaults in use")]));

  const cols = [["ts", "When"], ["mode", "Mode"], ["stt_ms", "Hear ms"], ["first_token_ms", "Think ms"],
    ["first_tts_chunk_ms", "First voice ms"], ["prompt_tokens", "Prompt tok"], ["completion_tokens", "Reply tok"],
    ["cost_usd", "Cost $"], ["voice", "Voice"]];
  $("turns").replaceChildren(
    h("thead", {}, h("tr", {}, cols.map(([, label]) => h("th", {}, label)))),
    h("tbody", {}, data.turns.length ? data.turns.map((t) => h("tr", {}, cols.map(([key]) => {
      let v = t[key];
      if (key === "ts") v = when(v);
      if (key === "cost_usd" && v != null) v = Number(v).toFixed(5);
      return h("td", { class: typeof t[key] === "number" ? "num" : null }, v ?? "–");
    }))) : h("tr", {}, h("td", { colspan: cols.length, class: "empty" }, "No turns logged yet."))),
  );
}

$("rule-add").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = event.target.text;
  act(api("rules", { json: { text: input.value } }), "Rule added").then(() => { input.value = ""; });
});

const importance = $("episode-add").importance;
importance.addEventListener("input", () => { importance.nextElementSibling.value = importance.value; });
$("episode-add").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  act(api("episodes", { json: { text: form.text.value, importance: Number(form.importance.value) } }), "Episode added")
    .then(() => { form.text.value = ""; });
});

// -- Personality ----------------------------------------------------------------

// Typed-but-unsaved persona text survives a refresh; saving or resetting clears it.
let personaDirty = false;
$("persona").addEventListener("input", () => { personaDirty = true; });

function drawPrompt(data) {
  const area = $("persona");
  if (!personaDirty) area.value = data.persona;
  const pill = $("persona-source");
  pill.textContent = data.persona_source === "edited" ? "edited" : "shipped";
  pill.className = data.persona_source === "edited" ? "pill pill--sent" : "pill";
  // Mark each [memory N] so the rules stand out in the compiled text.
  const pre = $("compiled");
  pre.replaceChildren();
  const parts = data.compiled.split(/(\[memory \d+\])/);
  for (const part of parts) pre.append(/^\[memory \d+\]$/.test(part) ? h("mark", {}, part) : part);
}

$("persona-save").addEventListener("click", () => {
  personaDirty = false;
  act(api("persona", { json: { text: $("persona").value }, method: "PUT" }), "Persona saved: used from her next turn");
});
$("persona-reset").addEventListener("click", () => {
  if (confirm("Go back to the shipped persona? Your edit is removed.")) {
    personaDirty = false;
    act(api("persona", { method: "DELETE" }), "Back to the shipped persona");
  }
});

// -- Contacts --------------------------------------------------------------------

function drawContacts(list) {
  const form = $("contact-form");
  $("contacts").replaceChildren(
    h("thead", {}, h("tr", {}, ["Name", "Phone", "Relation", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, list.length ? list.map((c) => h("tr", {},
      h("td", {}, c.name),
      h("td", {}, c.phone),
      h("td", {}, c.relations.join(", ") || "–"),
      h("td", {}, h("div", { class: "row__actions" },
        h("button", { class: "btn btn--ghost btn--small", type: "button", onclick: () => {
          form.name.value = c.name;
          form.phone.value = c.phone;
          form.relation.value = c.relations[0] || "";
          form.country.value = c.country || "";
          form.name.focus();
        } }, "Edit"),
        h("button", { class: "btn btn--danger btn--small", type: "button", onclick: () => {
          if (confirm(`Remove ${c.name}? She won't be able to call them.`)) {
            act(api("contacts/remove", { json: { name: c.name } }), `${c.name} removed`);
          }
        } }, "Remove"),
      )),
    )) : h("tr", {}, h("td", { colspan: 4, class: "empty" }, "No contacts yet."))),
  );
}

$("contact-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const f = event.target;
  const json = { name: f.name.value, phone: f.phone.value, relation: f.relation.value, country: f.country.value };
  act(api("contacts", { json }), `${f.name.value} saved`).then(() => f.reset());
});

// -- Reminders ----------------------------------------------------------------------

const REPEAT_WORDS = { none: "Once", daily: "Every day", weekdays: "Weekdays", weekly: "Every week" };
const HAPPENED = { fired: "said", acked: "done", missed: "missed" };

function resetReminderForm() {
  const f = $("reminder-form");
  f.reset();
  f.replaces.value = "";
  $("reminder-save").textContent = "Add reminder";
  $("reminder-new").hidden = true;
}

function fillChoices(select, choices, value, unit) {
  select.replaceChildren(...choices.map((c) => h("option", { value: String(c) }, unit(c))));
  select.value = String(value);
}

function drawReminders(data) {
  fillChoices($("reminder-repeat"), data.repeat_choices, data.repeat_minutes, (c) => `${c} minutes`);
  fillChoices($("reminder-tries"), data.tries_choices, data.tries, (c) => (c === 1 ? "once" : `${c} times`));
  const f = $("reminder-form");
  $("reminders").replaceChildren(
    h("thead", {}, h("tr", {}, ["Say", "Repeat", "Next", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, data.reminders.length ? data.reminders.map((r) => h("tr", {},
      h("td", {}, r.text),
      h("td", {}, `${REPEAT_WORDS[r.repeat] || r.repeat}, ${r.time}`),
      h("td", {}, r.next),
      h("td", {}, h("div", { class: "row__actions" },
        h("button", { class: "btn btn--ghost btn--small", type: "button", onclick: () => {
          f.replaces.value = String(r.id);
          f.text.value = r.text;
          f.time.value = r.time;
          f.date.value = "";
          f.repeat.value = r.repeat;
          $("reminder-save").textContent = "Save changes";
          $("reminder-new").hidden = false;
          f.text.focus();
        } }, "Edit"),
        h("button", { class: "btn btn--danger btn--small", type: "button", onclick: () => {
          if (confirm(`Stop reminding her to "${r.text}"?`)) {
            act(api(`reminders/${r.id}/cancel`, { json: {} }), "Reminder cancelled");
          }
        } }, "Cancel"),
      )),
    )) : h("tr", {}, h("td", { colspan: 4, class: "empty" }, "No reminders yet."))),
  );
  $("reminder-history").replaceChildren(...(data.history.length
    ? data.history.map((e) => h("li", {}, `${when(e.ts)} · ${HAPPENED[e.kind] || e.kind} · ${e.text}`))
    : [empty("Nothing yet.")]));
}

$("reminder-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const f = event.target;
  const json = { text: f.text.value, time: f.time.value, date: f.date.value, repeat: f.repeat.value };
  if (f.replaces.value) json.replaces = Number(f.replaces.value);
  act(api("reminders", { json }), f.replaces.value ? "Reminder changed" : "Reminder added")
    .then(resetReminderForm);
});
$("reminder-new").addEventListener("click", resetReminderForm);

// -- Keys & settings ----------------------------------------------------------------

function drawKeys(serverKeys) {
  const purpose = Object.fromEntries(serverKeys.map((k) => [k.name, k.purpose]));
  purpose.GOOGLE_APPLICATION_CREDENTIALS_JSON = purpose.GOOGLE_APPLICATION_CREDENTIALS;
  let rows;
  if (shell) {
    $("keys-lead").textContent = "Stored encrypted on this tablet. Type or paste a new value to replace one; "
      + "leave a field empty to keep it. Saving restarts Saathi's engine with the new keys.";
    const status = JSON.parse(shell.keyStatus());
    rows = Object.entries(status).map(([name, set]) => keyRow(name, set, purpose[name], true));
    $("keys-actions").hidden = false;
  } else {
    $("keys-lead").textContent = "What the running engine has (values are never shown). On a laptop they come from "
      + "~/.saathi/env, loaded into the shell before `saathi run`; on the tablet, open this console from "
      + "the setup dialog to edit them.";
    rows = serverKeys.map((k) => keyRow(k.name, k.set, k.purpose, false));
    $("keys-actions").hidden = true;
  }
  $("keys").replaceChildren(...rows);
}

function keyRow(name, set, purpose, editable) {
  const multiline = name.endsWith("_JSON");
  const field = editable
    ? h(multiline ? "textarea" : "input", {
        name, rows: multiline ? 3 : null, type: multiline ? null : "password",
        autocomplete: "off", spellcheck: "false",
        placeholder: multiline ? "Paste the service-account JSON" : (set ? "Set. Type to replace" : "Not set"),
      })
    : null;
  return h("li", { class: "row key" },
    h("div", {}, h("div", { class: "key__name" }, name), h("div", { class: "muted small" }, purpose || "")),
    h("span", { class: set ? "pill pill--good" : "pill pill--bad" }, set ? "set" : "missing"),
    field);
}

$("keys-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!shell) return;
  const values = {};
  for (const field of event.target.querySelectorAll("input, textarea")) {
    if (field.value.trim()) values[field.name] = field.value.trim();
  }
  if (!Object.keys(values).length) { toast("Nothing typed: no key changed."); return; }
  const result = JSON.parse(shell.saveKeys(JSON.stringify(values)));
  toast(result.error ? result.error : `Saved ${result.stored.join(", ")}. Restarting the engine…`, Boolean(result.error));
  setTimeout(() => location.reload(), 4000);
});

for (const select of document.querySelectorAll("[data-pref]")) {
  select.addEventListener("change", () =>
    act(api("preferences", { json: { key: select.dataset.pref, value: select.value } }), "Setting saved"));
}

function drawSettings(prefs) {
  const defaults = {
    language: "english", tts_backend: "google-chirp3-hd", captions: "off", wake_word: "on",
    reminder_repeat_minutes: "10", reminder_tries: "3",
  };
  for (const select of document.querySelectorAll("[data-pref]")) {
    const value = prefs[select.dataset.pref] ?? defaults[select.dataset.pref];
    if ([...select.options].some((o) => o.value === value)) select.value = value;
  }
}

// -- Under the hood -----------------------------------------------------------

function drawLayout(data, overview, prompt) {
  const persona = overview.persona_source === "edited" ? "edited in this console" : "the shipped file";
  const r = data.retrieval;
  const layers = [
    ["Persona", `Who Saathi is and how she speaks: name, warmth, plain words. From ${persona}; edit it on the Personality tab.`],
    ["Memory instruction", data.code_sentences.memory_numbers],
    ["Rules", `Every active learned fact about her, numbered [memory N]. ${overview.counts.rules} right now. Edit on the Memory tab.`],
    ["Episodes", `The ${r.top_k} best-scoring things that happened (below). Picked again after every turn.`],
    ["Length cap", data.code_sentences.length_cap],
    ["Conversation so far", `A running summary of older talk, then the last ${data.window.exchanges} exchanges word for word (at most ~${data.window.tokens} tokens). Lost on restart.`],
    ["Language", "One line: \u201cReply in english.\u201d (or the language she chose). Set on Keys & settings or by asking her."],
    ["Mood", `Asked to start each reply with a tag the face shows and the voice never reads: ${data.moods.join(", ")}.`],
    ["Her words", "What the speech-to-text heard this turn."],
  ];
  $("stack").replaceChildren(...layers.map(([title, text], i) =>
    h("li", {}, h("span", { class: "layer__n" }, i + 1), h("div", {}, h("b", {}, title), h("p", {}, text)))));

  $("retrieval").textContent = `Out of every episode ever stored, ${r.top_k} are sent each turn. `
    + "Each one is scored on three things, so an old but important memory can still beat yesterday's small talk.";
  $("retrieval-facts").replaceChildren(...[
    ["Recency", `${r.recency_decay_per_hour} per hour since it happened (Park et al. 2023), so a day-old episode keeps ~${Math.round(100 * r.recency_decay_per_hour ** 24)}%.`],
    ["Importance", "1–10, given when the episode is written (\u201cher son visited\u201d high, \u201csaid hello\u201d low)."],
    ["Relevance", r.relevance_on
      ? "How close in meaning it is to her last words (a small on-device model, all-MiniLM-L6-v2)."
      : "Off on this device: the on-device model isn't installed, so ranking is recency + importance."],
  ].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));

  const f = data.files;
  $("files").replaceChildren(...[
    ["Data folder", f.data_dir],
    ["Memory database", `${f.database} (${(f.database_bytes / 1024).toFixed(0)} KB)`],
    ["Persona in use", f.persona_edited || `${f.persona_shipped} (shipped; Save on the Personality tab writes persona.txt in the data folder)`],
    ["Keys", shell ? "this tablet's encrypted app storage (Keys & settings tab)" : "~/.saathi/env"],
  ].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, h("code", {}, v))]));

  $("tables").replaceChildren(...data.tables.map((t) => h("article", { class: "tbl" },
    h("header", {}, h("code", {}, t.name), h("span", { class: "pill" }, `${t.rows} rows`)),
    h("p", {}, t.purpose),
    h("dl", { class: "facts" },
      h("dt", {}, "Written by"), h("dd", {}, t.written_by),
      h("dt", {}, "Sent to the model"), h("dd", {}, t.sent)),
    h("p", { class: "cols" }, ...t.columns.map((c) => h("code", {}, c))),
  )));
}

// -- load -----------------------------------------------------------------------------

async function loadAll() {
  try {
    const [overview, memory, prompt, people, keys, layout, reminders] = await Promise.all([
      api("overview"), api("memory"), api("prompt"), api("contacts"), api("keys"), api("layout"),
      api("reminders"),
    ]);
    drawOverview(overview);
    drawLayout(layout, overview, prompt);
    drawMemory(memory);
    drawPrompt(prompt);
    drawContacts(people);
    drawKeys(keys);
    drawReminders(reminders);
    drawSettings(memory.preferences);
  } catch (error) {
    toast(`Couldn't load: ${error.message}`, true);
  }
}

$("refresh").addEventListener("click", () => loadAll().then(() => toast("Up to date")));
const TABS = ["how", "memory", "hood", "persona", "contacts", "keys"];
const wanted = (location.hash || "").slice(1);
show(TABS.includes(wanted) ? wanted : "how");
loadAll();
