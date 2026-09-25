"""Finding the person she means when Whisper spells their name its own
way today: "Basudeb", "Basudev" and "Vasudev" are one person; so are
"Zhang Wei", "Wei Zhang" and "zhangwei".

Exists because this is what decides whether calling feels intelligent —
and because the failure that matters is asymmetric. Dialling the wrong
person is worse than a second of confirmation, and dialling a guess is
worse than saying "I don't have a number for them". So the output is not
"the best match" but a *band*: confident (say who, then dial), unsure (a
Choice card of the closest two or three), or none (say so; offer to
save). `tools/calling.py` acts on the band; nothing here dials.

How a pair is scored, in order:
1. Normalise: NFKD, strip accents and pinyin tone digits, lowercase.
2. Fold sounds that transliterate inconsistently onto one spelling:
   ph->f, dh->d, th->t, bh->b, sh->s, zh->z, ch->c, q->c, x->s, k->c,
   ee->i, oo->u, v->b, w->b (Hindi/Bengali "w" and "v" are one sound),
   a final ng->n, and doubled letters collapsed. Pinyin's zh/ch/sh and
   q/x cases fall out of the same table as the Indic ones.
3. Jaro-Winkler on the folded forms with spaces and hyphens removed,
   and again with a two-token name's order swapped (surname first or
   last). A one-word request is also scored against each word of a
   saved name ("Priya" -> "Priya Sharma"), so a first name alone is
   enough. Score = the maximum.

Contested: Soundex/Metaphone lost — English-centric, they collapse
"Priya" and "Paras" together and know nothing of pinyin. Edit distance
lost to Jaro-Winkler because names differ most at the end and agree at
the start, which JW's prefix bonus rewards. A library (jellyfish,
rapidfuzz) would be a dependency for ~40 lines. Thresholds come from the
corpus in `tests/test_call_match.py` and are recorded in DECISIONS.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")

CONFIDENT = 0.93
UNSURE = 0.79  # midpoint of the corpus gap: non-matches <= 0.783, ask-her >= 0.800
# Confident also needs daylight to the runner-up: two names this close
# to each other are exactly when she should be asked.
CONFIDENT_MARGIN = 0.05
MAX_CHOICES = 3

_FOLDS = (
    ("ph", "f"), ("dh", "d"), ("th", "t"), ("bh", "b"), ("gh", "g"), ("kh", "c"),
    ("sh", "s"), ("zh", "z"), ("ch", "c"), ("q", "c"), ("x", "s"), ("k", "c"),
    ("ee", "i"), ("oo", "u"), ("v", "b"), ("w", "b"), ("y", "i"),
)
_TONE_RE = re.compile(r"[1-5]")
_SPLIT_RE = re.compile(r"[\s\-_.']+")


def normalise(name: str) -> list[str]:
    """Lowercased, accent- and tone-free tokens."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _TONE_RE.sub("", folded.lower())
    return [t for t in _SPLIT_RE.split(folded) if t]


def fold(token: str) -> str:
    for src, dst in _FOLDS:
        token = token.replace(src, dst)
    if token.endswith("ng"):
        token = token[:-1]
    return re.sub(r"(.)\1+", r"\1", token)


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(0, max(len(a), len(b)) // 2 - 1)
    a_hits = [False] * len(a)
    b_hits = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hits[j] and b[j] == ch:
                a_hits[i] = b_hits[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    a_seq = [c for c, hit in zip(a, a_hits) if hit]
    b_seq = [c for c, hit in zip(b, b_hits) if hit]
    transpositions = sum(x != y for x, y in zip(a_seq, b_seq)) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


def _forms(tokens: list[str]) -> set[str]:
    folded = [fold(t) for t in tokens]
    forms = {"".join(folded)}
    if len(folded) == 2:
        forms.add(folded[1] + folded[0])
    return forms


def similarity(said: str, saved: str) -> float:
    said_tokens, saved_tokens = normalise(said), normalise(saved)
    if not said_tokens or not saved_tokens:
        return 0.0
    candidates = _forms(saved_tokens)
    if len(said_tokens) == 1 and len(saved_tokens) > 1:
        candidates |= {fold(t) for t in saved_tokens}
    return max(jaro_winkler(a, b) for a in _forms(said_tokens) for b in candidates)


@dataclass(frozen=True)
class Match:
    band: str  # "confident" | "unsure" | "none"
    ranked: tuple[tuple[float, object], ...]  # (score, item), best first, >= UNSURE only

    @property
    def best(self):
        return self.ranked[0][1] if self.ranked else None

    def choices(self) -> list:
        return [item for _, item in self.ranked[:MAX_CHOICES]]


def match(said: str, items: Iterable[T], name_of=lambda item: item) -> Match:
    scored: Sequence[tuple[float, T]] = sorted(
        ((similarity(said, name_of(item)), item) for item in items),
        key=lambda pair: pair[0],
        reverse=True,
    )
    plausible = tuple((s, i) for s, i in scored if s >= UNSURE)
    if not plausible:
        return Match("none", ())
    top = plausible[0][0]
    runner_up = plausible[1][0] if len(plausible) > 1 else 0.0
    if top >= CONFIDENT and top - runner_up >= CONFIDENT_MARGIN:
        return Match("confident", plausible[:1])
    if len(plausible) == 1:
        # A single plausible name that isn't confident: still a question,
        # never a dial. A Choice card needs two options, so the caller
        # confirms this one by name instead.
        return Match("unsure", plausible)
    return Match("unsure", plausible[:MAX_CHOICES])
