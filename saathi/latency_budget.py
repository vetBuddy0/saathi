"""SPEC.md's Budgets section, made into an actual gate: "A test reads
the `turns` table and fails the build when a turn exceeds budget... It
asserts on the 95th percentile, not the mean — a companion device's bad
turns are the ones a person notices, and a mean hides exactly those."

This module is the read-and-assert half; `screen/server.py`'s `_log_turn`
(item E) is the write half. Deliberately reads `turns` with the schema
exactly as it stands today (`eou_ms, engine_ms, first_audio_ms`) — no
dependency on the more granular per-stage columns proposed as a follow-up
SPEC.md diff (see `voice/engine/cascade.py`'s `TurnTimings` docstring).
`engine_ms` is SPEC's "Brain finishes" budget; `first_audio_ms` is
"Voice starts". Both are `NULL` on any row a session couldn't produce
granular timings for (see `_log_turn`) and are excluded from the
percentile, not treated as zero or dropped-whole-row — a partially
instrumented turn still says something about EOU even if it can't speak
to the rest.

Deliberately a pure function over rows, not a CI job that reads a real
device's `identity.sqlite3` — there is no such file in CI (this device
doesn't exist in a GitHub Actions runner), so the thing CI can actually
gate on is "does this percentile math and budget comparison behave
correctly against known data," which `tests/test_latency_budget.py`
covers with a real (temp-file) `IdentityStore` and synthetic rows. Gating
a real deploy against a real device's real turns is a separate, manual
step (or a future `saathi smoke` addition) — this module is what that
would call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from saathi.identity.store import IdentityStore

# SPEC.md, "Budgets". "Voice starts" is keyed by engine because the
# budget itself is ("500 ms (speech-to-speech) / 1,200 ms (cascade)") —
# two different numbers for two different VoiceSession implementations,
# not one number that happens to vary.
VOICE_STARTS_BUDGET_MS: dict[str, int] = {
    "realtime": 500,
    "cascade": 1200,
}
BRAIN_FINISHES_BUDGET_MS = 3000


def percentile_95(values: list[int]) -> float:
    """Nearest-rank 95th percentile — SPEC.md is explicit this must be
    the 95th percentile, not the mean, so a handful of bad turns can't
    hide inside an average. Raises on an empty list rather than
    returning a value that would silently pass any budget."""
    if not values:
        raise ValueError("no values to compute a percentile from")
    ordered = sorted(values)
    index = math.ceil(0.95 * len(ordered)) - 1
    index = max(0, min(index, len(ordered) - 1))
    return float(ordered[index])


@dataclass(frozen=True)
class BudgetResult:
    passed: bool
    turns_checked: int
    voice_starts_p95_ms: float | None
    voice_starts_budget_ms: int | None
    brain_finishes_p95_ms: float | None
    brain_finishes_budget_ms: int
    note: str = ""


def check_latency_budget(store: IdentityStore, engine: str = "cascade") -> BudgetResult:
    """Reads every logged `turns` row for `engine` and checks both of
    SPEC.md's Budgets against their 95th percentile. `passed` is `True`
    when there's simply no data yet to check (an empty `turns` table is
    an unproven system, not a failing one — the same "degrade sensibly
    when the store is empty" principle used everywhere else this session
    touched `IdentityStore`), with `note` saying so plainly rather than
    reporting a clean pass silently."""
    rows = store.read("turns", engine=engine)
    if not rows:
        return BudgetResult(
            passed=True,
            turns_checked=0,
            voice_starts_p95_ms=None,
            voice_starts_budget_ms=VOICE_STARTS_BUDGET_MS.get(engine),
            brain_finishes_p95_ms=None,
            brain_finishes_budget_ms=BRAIN_FINISHES_BUDGET_MS,
            note=f"no turns logged yet for engine={engine!r}",
        )

    voice_starts_values = [r["first_audio_ms"] for r in rows if r["first_audio_ms"] is not None]
    brain_finishes_values = [r["engine_ms"] for r in rows if r["engine_ms"] is not None]

    voice_starts_p95 = percentile_95(voice_starts_values) if voice_starts_values else None
    brain_finishes_p95 = percentile_95(brain_finishes_values) if brain_finishes_values else None

    voice_starts_budget = VOICE_STARTS_BUDGET_MS.get(engine)
    voice_starts_ok = (
        voice_starts_p95 is None
        or voice_starts_budget is None
        or voice_starts_p95 <= voice_starts_budget
    )
    brain_finishes_ok = brain_finishes_p95 is None or brain_finishes_p95 <= BRAIN_FINISHES_BUDGET_MS

    notes = []
    if not voice_starts_values:
        notes.append("no turns had first_audio_ms")
    if not brain_finishes_values:
        notes.append("no turns had engine_ms")
    if voice_starts_budget is None:
        notes.append(f"no Voice-starts budget known for engine={engine!r}")

    return BudgetResult(
        passed=voice_starts_ok and brain_finishes_ok,
        turns_checked=len(rows),
        voice_starts_p95_ms=voice_starts_p95,
        voice_starts_budget_ms=voice_starts_budget,
        brain_finishes_p95_ms=brain_finishes_p95,
        brain_finishes_budget_ms=BRAIN_FINISHES_BUDGET_MS,
        note="; ".join(notes),
    )
