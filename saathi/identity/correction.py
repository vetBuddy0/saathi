"""`correct_memory` — the spoken way to fix a wrong belief: "no, that's
wrong", "forget that", "that was my sister, not my daughter".

**Why this exists.** A device holding a wrong belief about an elderly
person with no way to fix it is the worst failure this product has
(SPEC.md, "Memory"). `profile.py` is the family's way in; this is hers.
It retires the rule she is contradicting through `IdentityStore.retire`
and records the correction itself as a high-importance `episodes` row,
so what she said reaches the next turn's context as a plain sentence
and outlives the rule it replaced.

**Why an `episodes` row and not a `corrections` table.** The option that
lost (TODO.md, "Where a correction is recorded"): a
`corrections(id, ts, rule_id, said)` table, a schema diff. `episodes`
is already what retrieval reads and what `reflect.py` reflects over; a
correction stored anywhere else would be invisible to both until more
plumbing was built. A wrong belief can also live in an episode rather
than a rule (the digest writes observations straight from what she
said), and there is no primitive that retires an episode — so the
correction episode is *the* fix for that case: it lands in the context
next to the wrong one, at importance 9, saying what is actually true.

**Which rule.** `cascade.py` honours one tool call per turn, so "list
the rules, then retire one" cannot happen inside a turn. Instead
`compile.py` sends each active rule as `[memory N] ...`, and the model
passes that `rule_id`. Without one, the handler falls back to matching
content words of `believed` (the model's quote of the wrong thing) and
then `said` against active rules, most recent first; with no overlap at
all it retires nothing, still records the correction, and tells the
model to ask her which thing was wrong. A fuzzy miss is a recorded
correction and one short question; a fuzzy hit on the wrong rule would
be a second wrong belief, so the threshold is any shared content word,
and ties go to the newest rule.

**Threads.** The handler runs inside `end_turn()` on the screen server's
executor thread, never the thread that opened `store`, so it opens its
own connection from `store.path` (the `preferences.threadsafe_reader`
pattern). Writing through the shared connection would pass every test
and raise `sqlite3.ProgrammingError` on the first real correction —
that exact bug shipped once (commit c4d3717). The correction episode is
written with `embedding` NULL on purpose: this is the hot path, and a
model load belongs between turns (`embed.backfill_embeddings` fills it).

Mirrors `tools/language.py`: a closure over the store, a JSON-serialisable
result whose `note` steers how the model phrases its confirmation
(DECISIONS.md 2026-09-18), and the description kept out of `Tool`
(`tools/llm_schema.py`). Permission scope `"memory"` — a fourth scope
after `"calls"`, `"music"`, `"preferences"`: changing what the device
believes about her is its own category of action.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from saathi.identity.digest import write_episode
from saathi.identity.store import IdentityStore, UnknownRow
from saathi.tools.registry import Tool

CORRECT_MEMORY_DESCRIPTION = (
    "Call this when she says something you have learned about her is wrong, or asks "
    "you to forget something: 'no, that's wrong', 'forget that', 'that was my sister, "
    "not my daughter'. Pass rule_id when the wrong thing is one of the numbered "
    "memories in your context. Always pass what she said."
)

# A correction outranks almost everything she says in passing. Park et
# al.'s 1-10 scale: 10 is a bereavement or a diagnosis; a correction
# sits just under that, because retrieving it beats retrieving the
# belief it corrects, every time.
CORRECTION_IMPORTANCE = 9.0

_STOPWORDS = frozenset(
    "a an the and or but not no yes that this it its is was were be been are am i you she "
    "he we they her his my your our their me him them of to in on at for with about from as "
    "by so do does did have has had if then than there here what which who when where why "
    "how forget remember wrong right true false said say says just really actually very "
    "please thank thanks okay ok oh saathi that's it's i'm isn't wasn't don't doesn't "
    "didn't can't won't any more never ever again".split()
)


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOPWORDS}


def _best_match(rules: list[dict[str, Any]], *texts: str) -> dict[str, Any] | None:
    """The active rule sharing the most content words with the first of
    `texts` that overlaps anything; newest rule wins ties."""
    for text in texts:
        words = _content_words(text or "")
        if not words:
            continue
        scored = [(len(words & _content_words(r["text"])), r["id"], r) for r in rules]
        scored = [s for s in scored if s[0] > 0]
        if scored:
            scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
            return scored[0][2]
    return None


def _correction_sentence(said: str, correction: str | None, retired: dict[str, Any] | None) -> str:
    parts = [f"She corrected Saathi: \"{said.strip()}\""]
    if correction and correction.strip():
        parts.append(f"What is true: {correction.strip().rstrip('.')}.")
    if retired is not None:
        parts.append(f"Saathi had wrongly believed: {retired['text'].strip().rstrip('.')}.")
    return " ".join(parts)


def make_correct_memory_tool(store: IdentityStore) -> Tool:
    path = store.path

    def _correct_memory(
        said: str,
        rule_id: int | None = None,
        believed: str | None = None,
        correction: str | None = None,
    ) -> dict[str, Any]:
        if not said or not said.strip():
            return {
                "status": "error",
                "detail": "said is required: what she actually said",
                "note": "Nothing was changed. Ask her, briefly, what was wrong.",
            }
        now = datetime.now(timezone.utc)
        with IdentityStore(path) as own:
            active = [r for r in own.read("rules") if r.get("active", 1)]
            retired: dict[str, Any] | None = None
            unknown_id = False
            if rule_id is not None:
                # Models emit "3" as often as 3; the schema says integer
                # but Registry.call passes arguments through untouched.
                try:
                    wanted = int(rule_id)
                except (TypeError, ValueError):
                    wanted = None
                retired = next((r for r in active if r["id"] == wanted), None)
                if retired is None:
                    # An explicit number that misses is stale context
                    # (that rule was retired last turn) or a slip. Never
                    # fall through to the fuzzy matcher here: guessing a
                    # different rule from her words would be a second
                    # wrong belief. Record, and ask.
                    unknown_id = True
            elif believed or said:
                retired = _best_match(active, believed or "", said)
            # The trail first, then the change: if the second write
            # fails, a rule retired with no record of why is worse than
            # a recorded correction whose rule is still active (the next
            # turn's context carries her words either way).
            episode_id = write_episode(
                own,
                _correction_sentence(said, correction, retired),
                CORRECTION_IMPORTANCE,
                now=now,
                embedder=None,
            )
            if retired is not None:
                try:
                    own.retire("rules", retired["id"], now)
                except UnknownRow:
                    retired = None

        if retired is not None:
            return {
                "status": "retired",
                "retired": {"id": retired["id"], "text": retired["text"]},
                "episode_id": episode_id,
                "note": (
                    "The wrong memory has been removed and her correction recorded; it "
                    "takes effect from her next turn. Confirm briefly and warmly in one "
                    "sentence, without saying any memory number aloud and without "
                    "apologising at length."
                ),
            }
        prefix = (
            "That memory number matched nothing active (it may already have been "
            "corrected). "
            if unknown_id
            else ""
        )
        return {
            "status": "recorded",
            "retired": None,
            "episode_id": episode_id,
            "note": prefix + (
                "Her correction has been recorded and will be remembered, but no specific "
                "learned memory matched it. If it is unclear what she meant, ask her gently "
                "which thing was wrong -- one short question, no memory numbers."
            ),
        }

    return Tool(
        name="correct_memory",
        schema={
            "type": "object",
            "properties": {
                "said": {
                    "type": "string",
                    "description": "What she said, as close to her own words as possible.",
                },
                "rule_id": {
                    "type": "integer",
                    "description": (
                        "The memory number from your context, when she is correcting one "
                        "of the numbered things you learned."
                    ),
                },
                "believed": {
                    "type": "string",
                    "description": (
                        "The wrong belief in your own words, if you cannot give a number."
                    ),
                },
                "correction": {
                    "type": "string",
                    "description": "What is actually true instead, if she said.",
                },
            },
            "required": ["said"],
        },
        permission="memory",
        handler=_correct_memory,
    )
