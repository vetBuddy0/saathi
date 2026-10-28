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
  $("rules").replaceChildren(...(active.length ? active.map(ruleRow) : [empty("No rules yet. She learns them from conversations, or add one above.")]));
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
  const defaults = { language: "english", tts_backend: "google-chirp3-hd", captions: "off" };
  for (const select of document.querySelectorAll("[data-pref]")) {
    const value = prefs[select.dataset.pref] ?? defaults[select.dataset.pref];
    if ([...select.options].some((o) => o.value === value)) select.value = value;
  }
}

// -- load -----------------------------------------------------------------------------

async function loadAll() {
  try {
    const [overview, memory, prompt, people, keys] = await Promise.all([
      api("overview"), api("memory"), api("prompt"), api("contacts"), api("keys"),
    ]);
    drawOverview(overview);
    drawMemory(memory);
    drawPrompt(prompt);
    drawContacts(people);
    drawKeys(keys);
    drawSettings(memory.preferences);
  } catch (error) {
    toast(`Couldn't load: ${error.message}`, true);
  }
}

$("refresh").addEventListener("click", () => loadAll().then(() => toast("Up to date")));
const TABS = ["how", "memory", "persona", "contacts", "keys"];
const wanted = (location.hash || "").slice(1);
show(TABS.includes(wanted) ? wanted : "how");
loadAll();
