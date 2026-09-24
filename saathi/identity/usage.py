"""What each voice has actually cost, folded from the `turns` table.

Exists because the Ctrl+L panel showed a list price per backend
("$30 / 1M chars") and nothing else: the user asked for cost per voice
*from real usage*, visible while choosing, not discovered on a bill.
`docs/completed/C-tts-backends.md` recorded that wiring as never
started; this is it. The read half only -- the write half is
`voice/engine/cascade.py` filling `TurnTimings.voice/tts_chars/
tts_cost_usd` and `screen/server.py`'s `_log_turn` storing them.

A pure fold over `store.read("turns")` in Python, the same shape as
`latency_budget.check_latency_budget`, rather than a `SUM(...) GROUP BY`
in SQL: `IdentityStore.read` is exact-match filters only, and adding
aggregate queries to one of the five interfaces for a table of a few
thousand rows would be a conversation about the interface, not a
feature. Rows logged before the columns existed have `voice` NULL and
are simply not attributed -- "spent so far" means since the picker, and
the panel says so rather than guessing which voice an old turn used.
"""

from __future__ import annotations

from dataclasses import dataclass

from saathi.identity.store import IdentityStore


@dataclass(frozen=True)
class TTSUsage:
    turns: int
    chars: int
    cost_usd: float


def tts_usage(store: IdentityStore) -> dict[str, TTSUsage]:
    """Per-voice totals keyed by voice id, over every logged turn that
    named one. A turn with a voice but a NULL `tts_chars`/`tts_cost_usd`
    (a partially instrumented session) still counts as a turn of that
    voice and contributes zero to the sums -- undercounting spend is the
    honest direction; inventing a figure is not. Voices with no turns
    are absent, not zero: the caller decides how to say "not used yet"."""
    totals: dict[str, tuple[int, int, float]] = {}
    for row in store.read("turns"):
        voice = row.get("voice")
        if not voice:
            continue
        turns, chars, cost = totals.get(voice, (0, 0, 0.0))
        totals[voice] = (
            turns + 1,
            chars + int(row.get("tts_chars") or 0),
            cost + float(row.get("tts_cost_usd") or 0.0),
        )
    return {
        voice: TTSUsage(turns=turns, chars=chars, cost_usd=cost)
        for voice, (turns, chars, cost) in totals.items()
    }
