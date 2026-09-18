# Working rules

`SPEC.md` is the contract. Read it first, every session.

Only project-specific rules here — general engineering judgement is assumed.

## Never

- A device name in code or config (`plughw:3,0`, `card 2`, `/dev/input/event4`).
  Devices are detected. This is the bug that cost the most time previously.
- Changing a test so it passes. If a test is wrong, say so and stop.
- Status text under the face ("Listening…"). It will feel natural to add it.
  Don't — a person doesn't display a status label.
- The voice engine executing an action. It emits intent; the core validates;
  the tool executes.
- Sending personality floats to the model. Floats are storage; sentences are
  what the model receives.
- Compiling context during a turn. Between turns only — the hot path is the
  latency we're protecting.
- Adding an agent framework, vector DB server, message bus or container.

## The five interfaces

`VoiceSession`, `Transport`, `Tool`, `Face`, `IdentityStore`.

Everything else changes freely. Changing these is a conversation, not a commit.
Reaching through one — the engine touching the UI, a tool called without its
permission check — silently removes the architecture.

## Docstrings

Every module opens with **why it exists**, not what it does. Where a design
decision was contested, record the option that lost and why. That is the thing
that gets lost and re-litigated.

## Done means

Full suite green on x86 and arm64, new logic has tests, undefined-name check
passes, latency budget passes. Not "verified manually".

## Blocked means

Say what you tried and stop. A clear "this is blocked because X" beats a
plausible implementation that doesn't work. Never narrow the spec silently —
if something can't be done as written, raise it.

## Autonomy

Decide alone anything reversible in under an hour, and record it in
DECISIONS.md. Ask only about money, new vendors, the five interfaces, or
anything that changes what she hears. Never stop for confirmation. Never ask
the same thing twice.
