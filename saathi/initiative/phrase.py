"""Turns an allowed initiative into words — the one place a model is
involved after `policy.py` has already decided something is worth
saying, and the most dangerous step in the whole initiative pipeline.

The first dry run's phrasing (a throwaway prompt in a scratch script)
produced "It looks like Priya is planning to call this evening" and
"I've made a special arrangement to ensure tomorrow's scan goes
smoothly." Neither fact existed. Delivered to someone living alone,
the first has her waiting for a phone that never rings; the second
she'd believe. This is not a tone problem — it's the same fabrication
item D's bake-off flagged, arriving unprompted.

So the constraints here are hard, and enforced in code, not just asked
for in the prompt:

- The model receives **only** the reason and the source episode text.
  No persona, no memory, nothing else it could draw a "fact" from.
- **No new named entities.** `check()` flags any capitalized word that
  isn't in the source (case-insensitively) — sentence-initial words
  get a small allowlist of ordinary starters so "Good morning" isn't a
  proper noun, but "Priya is…" at the start of a sentence still is.
- **No claims about the future or about actions taken.** `check()`
  flags "will", "'ll", "going to", "planning to", "I've made", "I did",
  "arrangement" — *regardless* of whether the source contains the same
  construction (a source saying "planning to visit Saturday" does not
  license "planning to call this evening") — and time words like
  "tomorrow" or "this evening" unless the source itself says them. It
  may ask, recall, or acknowledge ("Shall I…?", "You mentioned…"); it
  may not announce, promise, or arrange.
- A rejected utterance is retried once, then **falls back to a
  template** with the source inserted verbatim. A slightly stiff
  sentence that's true beats a warm one that isn't.

The check is a heuristic, deliberately: it's pure local text
processing (no second model call to grade the first — the tick and its
aftermath stay model-light and inspectable), so it will occasionally
flag an innocent capitalized word and force a template. That's the
right failure direction. What it must never do is pass "Priya" when
the source never mentioned her; `tests/test_initiative_phrase.py` pins
that with the exact utterances from the dry run.

Nothing here speaks. `phrase()` returns text; wiring it to `say()` is
still a separate, later step — and per SPEC.md, one that doesn't happen
until this holds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ordinary ways a sentence starts that aren't names. A capitalized word
# *not* at the start of a sentence needs no allowlist -- it's a proper
# noun unless the source has it.
_SENTENCE_STARTERS = frozenset(
    """
    i i'm i'll i've i'd a an the it it's its this that these those there here
    good hi hello hey oh well yes no thank thanks sorry please just
    you your you're you've you'll we we're our let let's
    how are is do did does have has had was were when what why where which who
    would could should shall can may might must
    take time remember don't didn't doesn't isn't aren't wasn't
    maybe perhaps so and but if because while until once since before after
    today tonight tomorrow morning evening afternoon now then
    all some one none every each any much many more most only very really
    such like also still even again not
    my me mine her his their
    in on at for with about of to from by as
    try make get keep rest sit go come feel hope know think tell ask call be
    nothing something anything everything nobody somebody anybody everybody
    """.split()
)

# Future tense and taken-action claims. Word-boundaried, case-insensitive.
_CLAIM_PATTERNS = [
    r"\bwill\b",
    r"'ll\b",
    r"\bgoing to\b",
    r"\bgonna\b",
    r"\bplan(?:s|ning|ned)? to\b",
    r"\b(?:is|are|was|were|am) planning\b",
    r"\bI(?:'ve| have|'d| had)? (?:made|arranged|set|booked|called|noted|done|told|asked"
    r"|sent|put|fixed|sorted|organi[sz]ed)\b",
    r"\barrangement\b",
    r"\barranged\b",
    r"\bI did\b",
    r"\bI(?:'ve| have) got\b",
    r"\bwe(?:'ve| have) got you covered\b",
]

# Time references that assert something about when, unless the source
# itself says so.
_TIME_WORDS = [
    "tomorrow",
    "tonight",
    "this evening",
    "this afternoon",
    "this morning",
    "later today",
    "next week",
    "coming up",
]

# "I" is capitalized wherever it sits and is never a name.
_ALWAYS_ALLOWED = frozenset({"i", "i'm", "i'll", "i've", "i'd"})

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


@dataclass(frozen=True)
class Violation:
    kind: str  # "named_entity" | "claim" | "time"
    text: str


def _normalize(text: str) -> str:
    return text.replace("’", "'").lower()


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check(utterance: str, source: str) -> list[Violation]:
    """Every way `utterance` goes beyond `source`. Empty means it holds."""
    source_norm = _normalize(source)
    source_words = set(_WORD_RE.findall(source_norm))
    violations: list[Violation] = []

    for sentence in _sentences(utterance):
        words = _WORD_RE.findall(sentence)
        for index, word in enumerate(words):
            if not word[0].isupper():
                continue
            lowered = _normalize(word)
            if lowered in source_words or lowered in _ALWAYS_ALLOWED:
                continue
            if index == 0 and lowered in _SENTENCE_STARTERS:
                continue
            violations.append(Violation("named_entity", word))

    # Claims are flagged regardless of the source. A source that says
    # "Priya is planning to visit Saturday" does not license "Priya is
    # planning to call this evening" -- the construction matched, the
    # fact didn't. Recalling a stated plan has to be phrased without
    # future tense ("You mentioned Priya's visit on Saturday"), or it
    # falls to the template. Stricter than strictly necessary, on
    # purpose: the cost of a false positive is a stiff sentence; the
    # cost of a false negative is her waiting for a phone that never
    # rings.
    utterance_norm = _normalize(utterance)
    for pattern in _CLAIM_PATTERNS:
        for match in re.finditer(pattern, utterance_norm, flags=re.IGNORECASE):
            violations.append(Violation("claim", match.group(0)))
    for phrase in _TIME_WORDS:
        if phrase in utterance_norm and phrase not in source_norm:
            violations.append(Violation("time", phrase))

    return violations


_PROMPT = """You are a warm, patient companion device for an elderly person living \
alone. Something has been decided worth mentioning to her. You know exactly this, and \
nothing else:

Reason: {reason}
What she said or what was noted: {source}

Say it to her in one or two short, plain sentences, the way a caring neighbour would. \
Rules, all of them hard:
- Use only what is in the reason and the note above. Do not add any fact.
- Do not use any person's name, place, or day unless it appears above.
- Do not say anything will happen, is going to happen, or is planned.
- Do not say you did, made, arranged, set, or booked anything.
- You may ask her something, recall what she said, or acknowledge how she might feel.
Reply with the words to say and nothing else."""


def template(kind: str, reason: str, source: str) -> str:
    """The fallback: true by construction, because it's the source
    itself. Stiff on purpose."""
    if kind == "scheduled":
        text = re.sub(r"\s*\[reminder_id=\d+\]", "", reason)
        text = re.sub(r"^Reminder due:\s*", "", text)
        return f"A reminder: {text}."
    return f"I was thinking about something you mentioned: {source}"


def phrase(client, kind: str, reason: str, source: str, *, attempts: int = 2) -> str:
    """Model-phrased if it passes `check()` within `attempts`, else the
    template. Only ever called for a candidate `policy.py` already
    allowed — never from a scheduler tick."""
    for _ in range(attempts):
        completion = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=[
                {"role": "user", "content": _PROMPT.format(reason=reason, source=source)}
            ],
        )
        candidate = (completion.choices[0].message.content or "").strip().strip('"')
        if candidate and not check(candidate, f"{reason} {source}"):
            return candidate
    return template(kind, reason, source)
