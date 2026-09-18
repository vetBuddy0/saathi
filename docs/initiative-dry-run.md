# Initiative dry run — 2026-09-19 (updated: clustering fix, before/after)

**Nothing here was spoken.** This runs the real `initiative/scheduler.py`
+ `initiative/policy.py` pipeline against a real `IdentityStore` and
writes down every candidate it produced — allowed or suppressed — and,
for the allowed ones, what Saathi would actually have said. Nothing in
this codebase calls `say()` from the initiative pipeline; that wiring
still doesn't exist (see `docs/completed/checkpoint-3.md`).

## The store is thin — this is synthetic

The real `identity.sqlite3` has nothing in it: no real conversations
have happened yet. So this seeds a **plausible, invented week** of
episodes and reminders (a scan, a daughter's visit, medication, sore
knees, a grandson's exam call — the kind of texture a real week would
produce, not a real one) into a fresh, temporary store, and runs the
real pipeline against that. Every episode/reminder/rule/utterance below
is fictional. The mechanism producing them is real.

## Where the model is, and isn't, involved

Three separate steps, on purpose:

1. **Reflection** (`identity/reflect.py`) — a real Groq call, run once
   over the whole seeded week, offline. Not part of a scheduler tick;
   a separate maintenance pass that turns episodes into rules ahead of
   time.
2. **The scheduler tick itself** (`scheduler.propose_candidates()` +
   `policy.evaluate()`) — **pure local SQL, verified with a test that
   makes `groq.Groq` explode if it's ever touched**
   (`tests/test_initiative_no_model_calls.py`). Neither module imports
   `groq`.
3. **Phrasing** — only for a candidate `policy.py` has *already*
   allowed, one more real Groq call turns its bare `reason` into
   something a person would actually say. A suppressed (or, now,
   held-back) candidate never reaches this step.

## What the first version of this file exposed

The first run of this dry run — seeded week, real `reflect()`, real
scheduler + policy, `PolicyContext(presence=True)` for every tick —
surfaced **five "noticed" candidates in the same 09:00 tick**, all
allowed at once, because reflecting once over the whole seeded week
produced five rules simultaneously and the policy gate at the time had
no concept of "how many, how close together." That's exactly the
"device that comments on everything" failure SPEC.md's `policy.py`
section warns against, just concentrated into one moment instead of
spread across a session. Flagged as a real finding, not fixed then;
fixed now, four rules deep in `initiative/policy.py`:

1. **One utterance per tick.** Highest-scoring candidate wins; the rest
   are logged `"lost to a higher-scoring candidate this tick"` and
   compete again next tick — not a queue, not dropped.
2. **Cooldown**, 90 minutes by default (`PolicyConfig.cooldown_minutes`),
   between any two fired utterances — except a due reminder, which is
   exempt (a pill reminder shouldn't wait on a cooldown built for
   conversational restraint).
3. **Daily cap**, 3 by default (`PolicyConfig.daily_cap`). Once hit,
   nothing more fires that day, "whatever the score" — deliberately
   including reminders; there is no cap exemption for them, only a
   cooldown one. See "A real tension worth deciding," below.
4. **Expiry.** A "noticed" candidate more than `PolicyConfig.noticed_expiry_days`
   (default 2) past its source episode's timestamp is dropped —
   `suppressed_by` starts with `"expired:"`, the one other terminal
   state besides firing. Asking about Thursday's scan is kind on Friday
   and strange on Sunday; there's no date-parsing (that would need a
   model call), just how long ago it was *observed*.

## Before / after, same seeded week, side by side

**Before** (first version of this file — presence=True every tick, no
scoring, no cooldown, no cap, no expiry):

| Time | Kind | Result |
|---|---|---|
| 09/14 09:00 | scheduled | ALLOWED — morning tablet |
| 09/14 09:00 | noticed | **ALLOWED** — Priya's support |
| 09/14 09:00 | noticed | **ALLOWED** — knee pain + scan anxiety |
| 09/14 09:00 | noticed | **ALLOWED** — scan investigating knee |
| 09/14 09:00 | noticed | **ALLOWED** — sweets/mood |
| 09/14 21:00 | scheduled | ALLOWED — evening tablets |
| 09/15 09:00 | scheduled | ALLOWED — leave-in-time reminder |
| *(rest of the week)* | — | nothing (all sources exhausted at once) |

**Five things said in the same breath, then silence for six days.**

**After** (this run, same seeded week, real output):

| Time | Kind | Result |
|---|---|---|
| 09/14 09:00 | scheduled | ALLOWED — morning tablet |
| 09/14 09:00 | noticed | lost to the reminder this tick |
| 09/14 09:00 | noticed | lost to the reminder this tick |
| 09/14 21:00 | scheduled | ALLOWED — evening tablets |
| 09/14 21:00 | noticed | lost to the reminder this tick |
| 09/14 21:00 | noticed | lost to the reminder this tick |
| 09/15 09:00 | scheduled | ALLOWED — leave-in-time reminder |
| 09/15 09:00 | noticed | lost to the reminder this tick |
| 09/15 09:00 | noticed | lost to the reminder this tick |
| 09/15 21:00 | noticed | **ALLOWED** — scan anxiety (no reminder competing this tick) |
| 09/15 21:00 | noticed | lost to the scan-anxiety candidate this tick |
| 09/16 09:00 | noticed | **ALLOWED** — Priya/family support (its turn, finally) |

**One thing per tick, reminders always taking priority when they
compete, the two noticed insights spaced a full tick apart instead of
landing together.** (This run's `reflect()` call happened to produce
two rules rather than the first run's five — real LLM variance between
runs, not a change in method — but the fix is what matters here: however
many "noticed" candidates exist, at most one fires per tick, and losers
retry rather than vanish.)

Full phrased utterances for the "after" run:

- *(09/14 09:00)* "Good morning. It's time for your morning blood
  pressure tablet."
- *(09/14 21:00)* "It's about time for your evening tablets. Shall I
  come over to watch you take them?"
- *(09/15 09:00)* "Good afternoon, just a gentle nudge to remember you
  have that scan on Thursday. Try to get yourself ready to leave on
  time so you aren't rushed!"
- *(09/15 21:00)* "I heard the weather is turning a bit messy, so why
  don't I call a friend to drive you to the scan? I'll stay right here
  with you while you take it slow, so you don't have to worry about a
  thing."
- *(09/16 09:00)* "It will be lovely to sit down and share a meal with
  your daughter soon. We can take our time and just enjoy the chat
  together."

**A real limitation of this simulation, not the fix:** ticks here are
twice daily (~09:00, ~21:00), 12 hours apart — comfortably past the
90-minute cooldown every time, so the seeded week's own timeline never
actually shows cooldown *binding* anything. What it does show clearly
is rule 1 (one per tick, reminders first) doing real work. Cooldown,
cap, and expiry are demonstrated directly below instead, against the
same real code.

## Cooldown, cap, and expiry — direct demonstration

Same real `policy.evaluate()`, controlled scenarios, real output:

**Cooldown (default 90 minutes):**

| When | Candidate | Result |
|---|---|---|
| t = 0 | a "noticed" candidate | fires |
| t = +30 min | a second "noticed" candidate | held back — *"cooldown (90 min between utterances; 30 min since the last one)"* |
| t = +35 min | a due reminder | fires anyway — exempt |
| t = +91 min | the second "noticed" candidate again | **still held back** — *"56 min since the last one"* |

The last row is the subtle, correct part: the reminder at t=+35 wasn't
*blocked* by cooldown, but firing it still reset the clock for what
comes after it — 91−35=56 minutes, still under 90. A reminder is exempt
from *waiting on* cooldown; it doesn't stop *starting* one for whatever
comes next.

**Daily cap (default 3):**

| Utterance | Result |
|---|---|
| 1st (of the day) | fires |
| 2nd | fires |
| 3rd | fires |
| 4th, highest confidence of all four | **"daily cap reached (3/day)"** |
| A reminder, same day | **"daily cap reached (3/day)"** — no exemption |

**Expiry (default 2 days):**

| Candidate | Result |
|---|---|
| Observed 6 hours ago | fires |
| Observed 3 days ago (e.g. "Thursday's scan," now Sunday) | **"expired: missed its useful window (3 days since observed, limit 2)"** |

## A real tension worth deciding

The daily cap blocks reminders too, "whatever the score" — as
instructed, no exemption invented beyond the one explicitly given
(cooldown). But a medication reminder that can't fire because three
*other* things already used up today's cap is a real, concerning
outcome for a device some of whose users depend on it for that reminder,
not a hypothetical. Implemented exactly as specified, not overridden —
flagging it here rather than silently adding an unrequested carve-out.
Worth an explicit decision either way: cap the reminders as designed
(they'll wait until tomorrow's reset), or exempt reminders from the cap
the same way they're exempt from cooldown.
