"""Layer 3: a finished turn becomes an `episodes` row — and, in the same
call, the running conversation summary gets regenerated.

**Why this exists.** `compile.py` has scored retrieval over `episodes`
and `reflect.py` turns episodes into rules, but until this module
nothing in the codebase ever wrote an `episodes` row. The table had
zero rows on a device that had held 31 real conversations, so
`compile_context()` never got past `persona_stub.txt` and retrieval had
nothing to retrieve. Long-term memory wasn't broken so much as never
fed.

**Why one model call and not two.** Rating an episode's importance and
regenerating the conversation summary are different jobs, but they read
exactly the same material — the turn that just finished, plus whatever
fell out of the verbatim window. Two calls would send that material
twice, for two round trips, on every turn. They are asked for together
and come back as one JSON object.

**Why this never runs during a turn.** CLAUDE.md is explicit that
context compilation happens between turns only, and this is strictly
more expensive than compilation: it is a model call, not a query. It
runs from `cascade.py`'s post-`say()` background thread, alongside the
context refresh it feeds, and the episode it writes is visible to the
*next* turn's context — one turn stale, the same bargain `compile.py`
already documents.

**Failure is silence, not invention.** If the model returns something
that isn't the JSON asked for, this writes nothing and says so to the
caller. A fabricated episode is worse than a missing one: `reflect.py`
will later turn episodes into rules, `profile.py` shows those rules to
the family as things Saathi believes, and there is no way for anyone to
tell a hallucinated memory from a real one after the fact. An empty
memory is a device that hasn't learned yet; a wrong one is a support
call nobody can answer (SPEC.md).

**The option that lost:** deriving importance with a heuristic —
utterance length, keyword lists, whether a date was mentioned — to save
the call entirely. Rejected because importance is the axis retrieval
leans on hardest while `embedding` is still NULL everywhere (no
embedding provider on this account, so `relevance` contributes a flat
zero — see `compile.retrieve_episodes`). With relevance dead, a
keyword-counting importance score would effectively *be* the retrieval
function, and it would rank "I took my tablets" over "the scan is on
Thursday" for no better reason than sentence length.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from saathi.identity.store import IdentityStore
from saathi.voice.conversation import Exchange

# Park et al. 2023's own 1-10 importance scale, kept as-is rather than
# rescaled to 0-1: `compile.retrieve_episodes` min-max normalizes across
# the candidate set before scoring, so the absolute range never reaches
# the ranking, and keeping the paper's numbers makes the prompt below
# say the same thing the paper does.
_IMPORTANCE_MIN = 1.0
_IMPORTANCE_MAX = 10.0

_DIGEST_PROMPT = """You are the memory of a companion device for an elderly person living \
alone. Two jobs, from the same material.

1. OBSERVATION: write one plain sentence recording what is worth remembering from the latest \
exchange, in the third person ("She mentioned her scan is on Thursday."). Record only what was \
actually said -- never infer, never embellish, never add detail that wasn't there. If nothing \
in the exchange is worth remembering later (small talk, a greeting, a thank you), return null \
for it rather than inventing something.

2. IMPORTANCE: rate that observation from 1 to 10, where 1 is wholly mundane (brushing teeth, \
the weather) and 10 is deeply significant (a bereavement, a diagnosis, a fall). Return null if \
the observation is null.

3. SUMMARY: rewrite the running summary of this conversation so it also covers the older \
exchanges listed below, which have just fallen out of verbatim memory. Two or three sentences, \
no more. Keep concrete details a later turn might need -- names, times, what was decided. If \
there are no older exchanges to fold in, return the existing summary unchanged.

Respond as JSON and nothing else:
{{"observation": "..." or null, "importance": N or null, "summary": "..."}}

Existing summary: {summary}

Older exchanges that have just fallen out of verbatim memory:
{unfolded}

The latest exchange:
She said: {user}
Saathi replied: {assistant}
"""


@dataclass(frozen=True)
class TurnDigest:
    """What one background digest produced. Any field may be absent:
    `observation`/`importance` are `None` when the model judged the turn
    not worth remembering (a real answer, not a failure), and `summary`
    is `None` when the call failed outright."""

    observation: str | None
    importance: float | None
    summary: str | None


def _format_unfolded(unfolded: list[Exchange]) -> str:
    if not unfolded:
        return "(none -- the summary does not need to change)"
    return "\n".join(f"She said: {e.user}\nSaathi replied: {e.assistant}" for e in unfolded)


def _clamp_importance(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # A model returning 11, or 0, or -3 is asking for something outside
    # the scale it was given. Clamped rather than rejected: the ordering
    # it intended is still usable, and throwing the whole observation
    # away over a bad number would lose real content to a formatting
    # slip.
    return max(_IMPORTANCE_MIN, min(_IMPORTANCE_MAX, number))


def digest_turn(
    client: Any,
    *,
    model: str,
    exchange: Exchange,
    unfolded: list[Exchange],
    summary: str,
) -> TurnDigest:
    """One model call: the episode worth keeping from `exchange`, its
    importance, and a summary that now also covers `unfolded`.

    Never raises for a model that misbehaves — a malformed response
    comes back as an all-`None` digest, and the caller writes nothing.
    A transport-level failure (no network, bad key) is left to
    propagate, because that is not the model being unreliable, it is the
    device being offline, and the caller's own thread boundary is where
    that gets handled.
    """
    prompt = _DIGEST_PROMPT.format(
        summary=summary or "(none yet -- this is the start of the conversation)",
        unfolded=_format_unfolded(unfolded),
        user=exchange.user,
        assistant=exchange.assistant,
    )
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    raw = (completion.choices[0].message.content or "").strip()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return TurnDigest(observation=None, importance=None, summary=None)
    if not isinstance(parsed, dict):
        return TurnDigest(observation=None, importance=None, summary=None)

    observation = parsed.get("observation")
    if not isinstance(observation, str) or not observation.strip():
        observation = None

    new_summary = parsed.get("summary")
    if not isinstance(new_summary, str) or not new_summary.strip():
        new_summary = None

    return TurnDigest(
        observation=observation.strip() if observation else None,
        # Importance without an observation is meaningless -- don't
        # carry a score for a memory that isn't being written.
        importance=_clamp_importance(parsed.get("importance")) if observation else None,
        summary=new_summary.strip() if new_summary else None,
    )


def write_episode(
    store: IdentityStore,
    observation: str,
    importance: float | None,
    *,
    now: datetime | None = None,
) -> int:
    """Append one `episodes` row. `entity_id` and `embedding` are left
    NULL on purpose, and neither is an oversight:

    - `entity_id` needs entity resolution ("her daughter" -> which
      `entities` row?), which nothing in this codebase does yet.
      Guessing one would attach real memories to the wrong person.
    - `embedding` needs an embedding model. This project's only model
      provider offers none, and CLAUDE.md rules out adding a vector DB
      or a second vendor to get one. `retrieve_episodes` already
      documents the exact consequence — relevance contributes a flat
      zero and ranking runs on recency + importance — so this degrades
      along a path that module already describes rather than a new one.
    """
    return store.append(
        "episodes",
        ts=(now or datetime.now(timezone.utc)).isoformat(),
        entity_id=None,
        text=observation,
        importance=importance,
        embedding=None,
    )
