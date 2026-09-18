"""State -> context block, run between turns only.

Not built at checkpoint 1 — nothing called a voice engine yet to hand
context to. Now the actual source of what the engine receives:
`cascade.py` no longer reads `persona_stub.txt` directly, it calls
`compile_context()`, which reads that same file as its enduring base
layer and folds in whatever `IdentityStore` currently holds on top of
it. `persona_stub.txt` stays the user's file to write — this module
never invents identity content of its own, only assembles what's
already there into sentences.

SPEC.md is explicit this never runs *during* a turn; the hot path is the
latency budget the whole system protects, and compiling context is not
free. This module itself has no opinion about *when* it's safe to call —
it's pure computation over whatever `store` holds at the moment it's
called. `cascade.py`'s `_refresh_compiled_context_in_background()` is
where "between turns, never during one" is actually enforced: it runs
once at construction (there's no previous turn to trigger it from, and
SPEC.md says the session must already hold context "when she starts
speaking") and again after every `_speak()` call returns, in a
background thread, so the *next* `end_turn()` reads whatever was
compiled from the turn that just finished — one turn stale on purpose,
per SPEC.md's own words: "Memory is one turn stale on purpose —
compiling in the hot path spends the latency we are protecting."

**Stored and sent are different** (SPEC.md, "Memory"). `episodes`,
`rules`, `preferences` are rows with confidence, timestamps and
provenance; what a model actually receives here is plain sentences —
`episode.text` and `rule.text` themselves, joined together, nothing
about confidence scores or embeddings leaking into the prompt. Floats
are storage; sentences are what the model receives (CLAUDE.md).

Retrieval follows *Generative Agents* (Park et al. 2023): recency +
importance + relevance, each independently normalized across the
candidate set before summing with equal weight — no principled reason
favors one axis over another for this device, and tuning weights without
real usage data would be guessing, not engineering. See
`retrieve_episodes()`'s docstring for exactly how each axis degrades
when its input is missing (a real, current state — nothing in this
codebase writes an `episodes` row or an embedding yet; `reflect.py`,
which would, is explicitly checkpoint 3 scope, not this one).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from saathi.identity.store import IdentityStore

_BASE_PERSONA_PATH = Path(__file__).parent.parent / "voice" / "persona_stub.txt"

# Park et al.'s own constant for their MemoryStream's recency decay.
# Nothing about this project's usage pattern argues for a different
# half-life, and picking one without real retention data to tune against
# would be guessing — kept as the paper's reference value on purpose.
_RECENCY_DECAY_PER_HOUR = 0.99
_TOP_K_EPISODES = 5

# The length cap is an engineering constraint (cost and fatigue both get
# worse with longer replies — the person building this said so
# explicitly), not identity content, so it lives here as code, not in
# persona_stub.txt, which is the user's file to write.
_LENGTH_CAP_SENTENCE = (
    "Keep replies to two sentences unless she has asked for something that "
    "genuinely needs more detail — long replies cost more and tire her, and "
    "both get worse together, so use no more sentences than the moment "
    "actually needs."
)


def _hours_since(ts: str, now: datetime) -> float:
    then = datetime.fromisoformat(ts)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max((now - then).total_seconds() / 3600.0, 0.0)


def _recency_score(ts: str, now: datetime) -> float:
    return _RECENCY_DECAY_PER_HOUR ** _hours_since(ts, now)


def _normalize(values: list[float]) -> list[float]:
    """Min-max to [0, 1] across the candidate set, per Park et al. All
    values tied (including all-zero, e.g. every episode has the default
    importance) is not treated as an error — everyone gets full credit
    on that axis, which is a constant offset and doesn't change the
    resulting ranking, just avoids dividing by zero."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-9:
        return 0.0
    return float(np.dot(a, b) / denom)


def retrieve_episodes(
    episodes: list[dict[str, Any]],
    *,
    now: datetime,
    query_embedding: np.ndarray | None = None,
    top_k: int = _TOP_K_EPISODES,
) -> list[dict[str, Any]]:
    """Generative Agents' retrieval score: recency + importance +
    relevance, each min-max normalized across `episodes` before summing.
    Degrades sensibly, not silently wrong, at each layer:

    - `episodes` empty -> `[]`, not an error. A fresh install, or a
      family that hasn't had Saathi long enough to have built memory,
      is a normal state, not a failure.
    - `query_embedding` is `None` (true everywhere in this codebase
      today — nothing calls this with one yet) -> relevance contributes
      a flat 0 to every candidate, so ranking runs on recency +
      importance alone. Correct, not a placeholder: there is no query to
      be relevant *to* until whatever calls this actually has one.
    - an episode's own `embedding` column is `NULL` (true for every
      episode today — nothing in this codebase writes one; that's
      `reflect.py`, checkpoint 3) -> that episode's relevance is 0,
      same reasoning, per-episode instead of across the board.
    """
    if not episodes:
        return []

    recency = [_recency_score(e["ts"], now) for e in episodes]
    importance = [float(e["importance"] or 0.0) for e in episodes]

    if query_embedding is None:
        relevance_n = [0.0] * len(episodes)
    else:
        relevance = []
        for e in episodes:
            blob = e.get("embedding")
            if blob is None:
                relevance.append(0.0)
                continue
            candidate = np.frombuffer(blob, dtype=np.float32)
            relevance.append(_cosine_similarity(query_embedding, candidate))
        relevance_n = _normalize(relevance)

    recency_n = _normalize(recency)
    importance_n = _normalize(importance)

    scored = [
        (recency_n[i] + importance_n[i] + relevance_n[i], episodes[i])
        for i in range(len(episodes))
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [episode for _score, episode in scored[:top_k]]


def compile_context(
    store: IdentityStore,
    *,
    now: datetime | None = None,
    query_embedding: np.ndarray | None = None,
    top_k: int = _TOP_K_EPISODES,
    base_persona_path: Path = _BASE_PERSONA_PATH,
) -> str:
    """The one translation this module exists for: rows with confidence
    and provenance (stored) into plain sentences a model acts on (sent).

    Degrades sensibly at every layer for a new/empty store: no active
    `rules` or no `episodes` just means those sections contribute
    nothing. For today's actual store (empty — nothing in v1 writes
    `episodes` or `rules` yet), this returns exactly
    `base_persona_path`'s content plus the length-cap sentence, which is
    correct: there's nothing else true about her yet to say.
    """
    now = now or datetime.now(timezone.utc)
    sentences = [base_persona_path.read_text().strip()]

    active_rules = [r for r in store.read("rules") if r.get("active", 1)]
    sentences.extend(r["text"].strip() for r in active_rules if r.get("text"))

    episodes = store.read("episodes")
    retrieved = retrieve_episodes(episodes, now=now, query_embedding=query_embedding, top_k=top_k)
    sentences.extend(e["text"].strip() for e in retrieved if e.get("text"))

    sentences.append(_LENGTH_CAP_SENTENCE)

    return " ".join(s for s in sentences if s)
