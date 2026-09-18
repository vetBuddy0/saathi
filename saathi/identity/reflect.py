"""Episodes -> rules, with provenance. Checkpoint 3.

This is where "noticed" initiative comes from (SPEC.md, "Initiative"):
reflection writes *she has a scan Thursday and is anxious*, and
`initiative/scheduler.py` queries `rules` for things worth following up
on. Every rule this module writes records `source_episode` — SPEC.md: a
companion that learns something wrong about someone's mother with no way
to correct it is a support call nobody can answer, and `identity/profile.py`
is that way to correct it.

Follows *Generative Agents* (Park et al. 2023) §Reflection, not a
paraphrase of it: reflection is a two-step LLM prompt over real
episodes, not a rules-of-thumb clustering heuristic.
  1. Given a batch of recent, important episodes (plain sentences —
     "stored and sent are different," same rule `compile.py` follows),
     ask the model for the 3 most salient high-level questions those
     episodes raise.
  2. For each question, retrieve the episodes most relevant to it
     (`compile.py`'s own `retrieve_episodes()` — the exact same
     recency+importance+relevance scoring `end_turn()`'s context uses,
     not a second implementation of the same idea), then ask the model
     for insights grounded in *only* those episodes, each insight
     citing which episode(s) it came from.

The paper's insights cite multiple source memories; `rules.source_episode`
is a single foreign key, not a list — this project's schema doesn't have
anywhere to put more than one. The *first* cited episode is recorded as
`source_episode` (still real, checkable provenance, not "trust me"), and
a proper multi-citation schema is exactly the kind of thing a SPEC.md
diff should decide, not something this module invents unilaterally — see
the proposed diff (not applied) in this commit's discussion.

`confidence` isn't a concept Park et al. numerically defines for
reflection; this module derives one honestly rather than inventing a
number that looks precise but isn't: `min(1.0, num_cited_episodes / 3)`
— an insight grounded in more of the retrieved episodes gets a higher
confidence, capped at 1.0. Simple, stated plainly, not dressed up.

Never runs during a turn, same rule as `compile.py` — this is
explicitly a background/maintenance pass (`initiative/scheduler.py` or
a future cron-like trigger calls it), not something `cascade.py` ever
calls directly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from groq import Groq

from saathi.identity.compile import retrieve_episodes
from saathi.identity.store import IdentityStore

# Same default as cascade.py's _LLM_MODEL -- one model, one config value,
# not a second constant to drift out of sync with item D's decision.
_REFLECTION_MODEL = "qwen/qwen3.8-27b"

_QUESTIONS_PROMPT = """You are reflecting on recent observations about someone you care for, \
as a companion device. Given the numbered statements below, propose the 3 most salient, \
high-level questions we could ask about the people and situations they describe. Respond as \
JSON: {{"questions": ["...", "...", "..."]}}. Do not answer the questions -- only propose them.

Statements:
{statements}
"""

_INSIGHTS_PROMPT = """You are reflecting on recent observations about someone you care for, \
as a companion device, to decide what higher-level things are worth remembering. Given the \
question below and the numbered statements that might answer it, propose up to 2 high-level \
insights that are directly supported by those statements. Do not speculate beyond what the \
statements say. Respond as JSON: {{"insights": [{{"text": "...", "cited": [1, 3]}}, ...]}} \
where "cited" lists the statement numbers (from the list below) that support each insight. If \
nothing in the statements actually supports a real insight, return an empty list -- inventing \
one is worse than finding nothing.

Question: {question}

Statements:
{statements}
"""


def _format_statements(episodes: list[dict[str, Any]]) -> str:
    return "\n".join(f"{i + 1}. {e['text']}" for i, e in enumerate(episodes))


def _propose_questions(client: Groq, episodes: list[dict[str, Any]]) -> list[str]:
    completion = client.chat.completions.create(
        model=_REFLECTION_MODEL,
        messages=[
            {
                "role": "user",
                "content": _QUESTIONS_PROMPT.format(statements=_format_statements(episodes)),
            }
        ],
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(completion.choices[0].message.content)
        questions = parsed.get("questions", [])
    except (json.JSONDecodeError, AttributeError):
        # A malformed response is a reason to reflect on nothing this
        # pass, not a reason to crash whatever background job called
        # this -- same "unavailable and visibly so" principle as
        # everywhere else, applied to an LLM response instead of a
        # missing dependency.
        return []
    return [q for q in questions if isinstance(q, str) and q.strip()]


def _propose_insights(
    client: Groq, question: str, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    completion = client.chat.completions.create(
        model=_REFLECTION_MODEL,
        messages=[
            {
                "role": "user",
                "content": _INSIGHTS_PROMPT.format(
                    question=question, statements=_format_statements(candidates)
                ),
            }
        ],
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(completion.choices[0].message.content)
        insights = parsed.get("insights", [])
    except (json.JSONDecodeError, AttributeError):
        return []

    results = []
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        text = insight.get("text")
        cited = insight.get("cited", [])
        if not isinstance(text, str) or not text.strip() or not isinstance(cited, list):
            continue
        # 1-indexed in the prompt, matching how episodes were numbered
        # in _format_statements(); anything out of range is dropped
        # rather than trusted -- a model citing "statement 9" when only
        # 5 were offered is not real provenance.
        cited_episodes = [
            candidates[i - 1] for i in cited if isinstance(i, int) and 1 <= i <= len(candidates)
        ]
        if not cited_episodes:
            continue  # an insight with no real citation isn't provenance, it's a guess
        results.append({"text": text.strip(), "cited_episodes": cited_episodes})
    return results


def reflect(
    store: IdentityStore,
    *,
    client: Groq | None = None,
    now: datetime | None = None,
    episode_limit: int = 30,
) -> list[dict[str, Any]]:
    """One reflection pass. Reads up to `episode_limit` of the store's
    episodes (all of them, if there are fewer), asks the model for
    salient questions, retrieves the episodes most relevant to each
    question via `compile.py`'s own retrieval scoring, asks for
    grounded insights, and writes each as a new `rules` row with
    `source_episode` set to its first cited episode.

    Degrades sensibly for an empty store (true everywhere in this
    codebase today — nothing writes an `episodes` row yet): returns `[]`
    without calling the model at all. Returns the list of newly written
    rule rows (as `IdentityStore.read()` shaped dicts), not just their
    ids, so a caller can log or review what was learned without a
    second read."""
    now = now or datetime.now(timezone.utc)
    if client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set in the environment")
        client = Groq(api_key=api_key)

    episodes = store.read("episodes")
    if not episodes:
        return []
    episodes = episodes[-episode_limit:]

    written: list[dict[str, Any]] = []
    for question in _propose_questions(client, episodes):
        relevant = retrieve_episodes(episodes, now=now, top_k=10)
        for insight in _propose_insights(client, question, relevant):
            cited_episodes = insight["cited_episodes"]
            confidence = min(1.0, len(cited_episodes) / 3)
            rule_id = store.append(
                "rules",
                text=insight["text"],
                confidence=confidence,
                learned_at=now.isoformat(),
                source_episode=cited_episodes[0]["id"],
                active=1,
            )
            written.extend(store.read("rules", id=rule_id))
    return written
