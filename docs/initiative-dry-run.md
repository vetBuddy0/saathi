# Initiative dry run — 2026-09-19

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
   over the whole seeded week, offline. This is *not* part of a
   scheduler tick; it's a separate maintenance pass that turns episodes
   into rules ahead of time, the same as it would run periodically on a
   real device.
2. **The scheduler tick itself** (`scheduler.propose_candidates()` +
   `policy.evaluate()`) — **pure local SQL, verified with a test that
   makes `groq.Groq` explode if it's ever touched**
   (`tests/test_initiative_no_model_calls.py`). No model call happens
   here, structurally — neither module even imports `groq`.
3. **Phrasing** — only for a candidate `policy.py` has *already*
   allowed, one more real Groq call turns its bare `reason` (e.g.
   `"Reminder due: take your evening tablets"`) into something a person
   would actually say out loud. A suppressed candidate never reaches
   this step at all.

## Methodology

Two simulated ticks per day (~09:00 and ~21:00) across an 8-day
seeded window, `PolicyContext(presence=True, quiet_hours=False,
busy=False)` for every tick — this dry run is about what the scheduler
*proposes* and *phrases*, not a presence-simulation exercise (no real
presence sensor exists yet — `scheduler.event_candidates()`'s own honest
limitation).

**A known artifact of this simulation, not the scheduler itself:**
reflection ran once, after the whole seeded week, so every "noticed"
rule already exists by the very first tick — all of them surface
together on day 1 rather than spread across the week the way they would
on a real device reflecting incrementally as episodes actually happen.
Real dedup logic (`scheduler.py`'s `_already_proposed_source_episodes`)
is genuinely exercised regardless: none of these five rules repeat on
any later tick.

## What the scheduler proposed and policy allowed

**2026-09-14 09:00 — scheduled**
> Reminder due: take your morning blood pressure tablet

Would say: *"Good morning! Just a gentle nudge that it's time to take
your morning blood pressure tablet."*

**2026-09-14 09:00 — noticed**
> She is relying on social support from her daughter, Priya, to help
> manage her anxiety, which is evidenced by Priya's visit, their
> extended conversation, and the sharing of treats.

Would say: *"It was lovely to see you two enjoying that time together
and the treats last week. How are you feeling today?"*

**2026-09-14 09:00 — noticed**
> Her physical discomfort, specifically knee pain, is impacting her
> routine and sleep, adding to her emotional strain while waiting for
> the scan results.

Would say: *"I've heard your knee hurting at night, and I want you to
know I'm thinking of you while you wait for those scan results. You've
been so brave dealing with this pain, and I hope you find some gentle
relief soon."*

**2026-09-14 09:00 — noticed**
> The user has a medical scan scheduled to investigate her knee pain,
> suggesting the physical symptoms are the reason for the appointment.

Would say: *"Good morning, I just wanted to remind you that your knee
scan is coming up soon. I hope it brings you some peace of mind and
helps us get to the bottom of that discomfort."*

**2026-09-14 09:00 — noticed**
> Sharing food, specifically sweets, and extended conversation during
> visits significantly boosts her mood, as evidenced by her reporting
> feeling "much better" after a visit where Priya brought sweets and
> they talked for hours.

Would say: *"You looked so happy after Priya left today. Just remember,
whenever you want to brighten your day, treating yourself to some
sweets and a nice chat really does the trick."*

**2026-09-14 21:00 — scheduled**
> Reminder due: take your evening tablets

Would say: *"It's time for your evening tablets. Would you like me to
put a glass of water over for you?"*

**2026-09-15 09:00 — scheduled**
> Reminder due: leave in time for Thursday's scan appointment
> check-in

Would say: *"Good morning, dearest. Just a gentle reminder to make sure
you set off with plenty of time to spare for your scan appointment on
Thursday. I have it noted down for you, so there is no need to worry
about the details."*

**2026-09-15 21:00 through 2026-09-21 21:00 (13 further ticks):
nothing proposed.** All three seeded reminders and all five reflected
rules were already proposed once — the real dedup logic held for the
rest of the week, which is the intended behavior (SPEC.md: restraint,
not commenting on the same thing every tick), not an empty result.

**A real, worth-noting finding:** five "noticed" candidates (four rules
plus context) all landed in the *same* 09:00 tick, because reflection
ran once and produced them all at once. On a real device, reflecting
incrementally, this clustering is less likely — but the scheduler
itself has no pacing logic to stagger multiple simultaneous "noticed"
proposals into separate turns even if it *did* happen. Five things
noticed at once is arguably exactly the "device that comments on
everything" failure mode SPEC.md's `policy.py` section warns against,
just concentrated in one moment instead of spread across a session.
Worth deciding before this is ever wired to actually speak: should
`policy.py` (or a v2 of it) cap how many "noticed" candidates it allows
per tick, or per day?

## What restraint actually looks like — direct demonstration

None of the ticks above happened to land during quiet hours or while
something else was going on, so the seeded week alone doesn't show
suppression. Run directly against `policy.should_speak()`, same real
code, same candidate (the evening-tablets reminder), four different
contexts:

| Context | Result |
|---|---|
| `presence=True` | **Speaks** — `"Reminder due: take your evening tablets..."` |
| `presence=True, quiet_hours=True` | Suppressed — `"quiet hours"` |
| `presence=None` (unconfirmed, the default) | Suppressed — `"presence not confirmed — no confirmed reason she's there to hear it"` |
| `presence=True, busy=True` | Suppressed — `"something else is already happening"` |

The default-context result is the one that matters most: with *nothing*
confirmed one way or the other, `policy.py` suppresses. That's SPEC.md's
"a reason to speak, not the absence of a reason to stay quiet," directly
verified, not just asserted in a docstring.
