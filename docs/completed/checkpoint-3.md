# Checkpoint 3 — started 2026-09-18

Scope per SPEC.md: `reflect.py` (episodes → rules), `profile.py`
(family-visible view), `initiative/scheduler.py` + `policy.py`. All four
built, tested, and verified against real data this session. **Nothing
is wired to actually speak unprompted** — explicit instruction, and the
right call regardless: this is the decision machinery, inspectable via
`initiatives` rows, not yet connected to `say()`.

## What was built

**`identity/reflect.py`** — real *Generative Agents* (Park et al. 2023)
reflection: propose salient questions over recent episodes, then for
each question retrieve the most relevant episodes (reusing
`compile.py`'s own recency+importance+relevance scoring — not a second
implementation) and ask for insights grounded in *only* those episodes,
each citing which one(s) support it. An insight with no valid citation
is dropped. `confidence = min(1.0, cited_episodes / 3)`. Verified
against a real Groq call: five real episodes produced five genuinely
coherent, grounded rules — see that commit's discussion for the actual
output.

**`identity/profile.py`** — `list_rules()`/`describe_rule()` fold each
rule together with its source episode's plain text, real and working.
`retract_rule()` always raises `RetractionNotSupported` — a real,
documented gap, not a silent no-op (see "Blocked" below).

**`initiative/scheduler.py`** — proposes candidates from SPEC.md's three
kinds: Scheduled (due reminders, real), Event (always `[]` — no
presence/call sensor exists in this codebase, and inventing one wasn't
in scope), Noticed (confident, active, not-yet-proposed rules from
`reflect.py`). Deduplicates against `initiatives` history so the same
reminder or insight doesn't get re-proposed every pass.

**`initiative/policy.py`** — SPEC.md's own sentence made literal: "a
reason to speak, not the absence of a reason to stay quiet."
`PolicyContext`'s defaults (presence unconfirmed, not "presence
absent") suppress everything until something actually confirms she's
there — tested directly: the all-defaults context suppresses even
though nothing said no either. Every candidate is logged to
`initiatives`, allowed or not, with why; `spoken` is always `0`.

## Verified

- `reflect.py` against a real Groq call (real episodes in, real
  grounded rules with real citations out).
- The full pipeline (`reflect` → `scheduler.propose_candidates` →
  `policy.evaluate`) run together against a real `IdentityStore`, real
  reminders, real reflected rules, real Groq call.
- 50 new tests (reflect: 8, profile: 8, scheduler: 21, policy: 13),
  full suite green.

## Blocked — two SPEC.md diffs proposed, not applied

Both surfaced by real code this pass needed, not speculative:

**1. `rules.source_episode` is a single foreign key; reflection's
insights can be grounded in more than one episode.** Today the *first*
cited episode is recorded — real, checkable provenance, just not
complete. Proposed:

```diff
-rules(id, text, confidence, learned_at, source_episode, active)
+rules(id, text, confidence, learned_at, active)
+rule_episodes(rule_id, episode_id)   -- one row per citation, append-only
-initiatives(id, ts, kind, reason, source_episode, spoken, suppressed_by)
+initiatives(id, ts, kind, reason, rule_id, source_episode, spoken, suppressed_by)
```

The `initiatives.rule_id` line was added 2026-09-19: "noticed" dedup
now keys on the rule, not its source episode (two rules from one
observation are two things to say), and today it has to use the rule's
*text* as that key because there's no column for the id. Real today,
not a placeholder — but a proper id is what it should be.

**2. `IdentityStore` has no update capability, and three real features
now need one:** retracting a rule (`rules.active`), completing/silencing
a reminder (`reminders.active`), and marking an initiative as actually
spoken (`initiatives.spoken`) once something is wired to speak it. The
`preferences.key` fix (make it append-only, latest row wins) doesn't
transfer to any of these — a family member retracting rule #47 needs
*that row*, not "the latest rule about this topic." Two shapes to
choose between, not decided here:

- **(a) A narrowly-scoped update primitive** on `IdentityStore` —
  something like `mark(table, row_id, **fields)`, deliberately not a
  general-purpose "update anything," scoped to flipping named columns
  by row id. Smallest change to the interface itself; changes what
  "create/append/read only" has meant since checkpoint 1.
- **(b) Append-only "event" tables per flag** — `rule_retractions(rule_id,
  retracted_at, reason)`, `reminder_completions(...)`,
  `initiative_spoken_events(...)` — readers compute the effective state
  by checking for a later event row, the same pattern `preferences`
  itself now uses. No `IdentityStore` interface change at all, more
  tables and more read-side logic.

Both are real options with real tradeoffs (interface simplicity vs.
schema sprawl); picking one is a "conversation, not a commit," per
CLAUDE.md's own words about the five protected interfaces.

## Not started

- `tools/builtin.py` (`remember`, `recall`, `time`, `set_reminder`) —
  named in SPEC's module map, not touched this pass. `recall` in
  particular would want `compile.py`'s retrieval machinery, which now
  exists and is ready for it.
- Wiring an allowed initiative to actual speech. Deliberately not done —
  read `initiatives` (or `policy.evaluate()`'s return value) to see what
  this pass would have said and why, before deciding whether/how to
  connect it to `say()`.
- Real presence/event detection. `event_candidates()` is a real,
  callable function that honestly returns nothing until one exists.
