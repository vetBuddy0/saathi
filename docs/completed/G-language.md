# G — Language switching

## What was built

The Ctrl+L panel (shared with item C) and the spoken "speak to me in
Mandarin" path now write through the exact same code —
`identity/preferences.py`'s `write_preference()`, called either by the
settings panel's `set_preference` WebSocket message or by
`tools/language.py`'s `set_language` tool, the project's first real
(non-stub) tool. `set_language` goes through `tools/registry.py`'s
permission check exactly like `call_contact`/`play_music` do — "the
voice engine never executes anything, it emits intent, the core
validates, the tool executes" (SPEC.md): `cascade.py`'s `on_intent()` is
real now, not a stub, but it only ever calls whatever callback `cli.py`
registered; `cli.py` owns the `Registry`, the permission grant, and the
actual execution.

## Two real bugs found and fixed via live testing, not caught by mocks

1. **A supported language detection used to silently override a
   just-set preference the moment she spoke a supported language
   again** — which made "speak to me in Mandarin" invisible on the very
   next turn if she then spoke English, the overwhelmingly common case
   (the request itself is usually made in whatever language she was
   already speaking). Fixed: a supported stored preference now pins the
   language for every following turn, overriding organic detection,
   until explicitly changed again. Verified live, before and after: the
   original code reverted to English on turn 2; the fix correctly
   stayed on Mandarin.
2. **The tool's first "ok" result produced a confusing spoken reply**
   ("I'll continue in English as instructed") that sounded like a
   refusal, because the model didn't know the switch (for *next* turn)
   had already succeeded. Fixed by adding a plain-language note to the
   tool result explaining the timing — confirmed live afterward with a
   natural, positive confirmation instead.

## Verified

Real, multi-turn, real Groq calls: turn 1 (English audio, asks to
switch to Mandarin) correctly writes the preference and gives a natural
confirmation reply in English (this turn's own reply language, per
"effective next turn," not immediately); turn 2 (the same English audio
again) correctly replies in Mandarin.

## Decided rather than told

- `set_language`'s permission scope is `"preferences"`, a new scope —
  `call_contact`/`play_music` use `"calls"`/`"music"`, a different
  category of action (no external-world consequence). Granted
  unconditionally in `cli.py`, unlike calls/music.
- Tool-calling handles only the first `tool_call` in a response, not
  multiple — `set_language` is the only real tool this project has;
  nothing exercises multi-tool-call turns yet, flagged as a real,
  unhandled limitation in code rather than silently dropped.

## Not done / left open

- The `preferences.key` schema fix this whole feature depended on was
  proposed, then explicitly authorized and applied later in the
  session — see `DECISIONS.md` and the schema-migration commit. Before
  that landed, a second preference write (panel or spoken) would have
  raised; it doesn't anymore.
- No multi-tool-call handling, as above.
- `saathi/tools/builtin.py` (`remember`, `recall`, `time`,
  `set_reminder`) is still unbuilt — `set_language` is this project's
  only real tool.
