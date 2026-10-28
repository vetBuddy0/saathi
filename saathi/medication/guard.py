"""The safety rules that can be checked on text: no doses, no telling
anyone to change, skip or double a medicine.

Why a rule list and not a model: these are the lines that must never be
crossed, and a check that is right nineteen times in twenty is a check
that lets the twentieth through. A pattern cannot fail to fire on what
it matches; what it misses is found in the adversarial list in
tests/test_medication_guard.py and added here. Lost: asking a model
"does this reply give medical advice?" -- probabilistic exactly where
being wrong is unacceptable, and a network hop on every reply.

Phase 1 uses it at the door: every `purpose` sentence an eMAR import
brings in is checked, so a purpose like "take 2 tablets for pain" is
rejected before anything could speak it. Phase 3 puts the same check
on every reply before it is spoken.

Coverage is per language, written for the four medication languages
(models.py) -- Singlish/English, Cantonese and Hokkien (written in Han
characters), Tamil. Erring towards blocking is intended: a blocked
sentence becomes "the nurse will explain", which is never harmful.
"""

from __future__ import annotations

import re

# A quantity: digits ("500", "1.5", "½") or a number word, then a unit.
# Digits may touch their unit ("500mg"); a word needs a space before it,
# or "Teresa Teng" reads as "ten g".
_EN_NUMBER = (
    r"(?:(?:\d+(?:[.,]\d+)?|½|¼)\s*-?\s*"
    r"|(?:one|two|three|four|five|six|seven|eight|nine|ten"
    r"|half(?:\s+a)?|a\s+half|another|extra|double)\s+)"
)
_EN_UNIT = (
    r"(?:mg|mcg|µg|ug|g|grams?|milligrams?|micrograms?|ml|mls|millilit(?:er|re)s?"
    r"|units?|iu|tabs?|tablets?|pills?|capsules?|caps?|puffs?|drops?|sachets?"
    r"|(?:tea|table)?spoons?(?:ful)?|doses?)"
)
_HAN_NUMBER = r"[0-9０-９零一二兩两三四五六七八九十百千半幾几]"
_HAN_UNIT = r"(?:毫克|微克|克|毫升|粒|片|顆|颗|包|滴|匙|錠|锭|次|劑|剂|份)"
_TA_UNIT = r"(?:மி\.?\s?கி|மில்லிகிராம்|மி\.?\s?லி|மாத்திரை(?:கள்)?|துளி(?:கள்)?|முறை)"

_DOSE = [
    re.compile(rf"(?<![\w.]){_EN_NUMBER}(?:(?:more|extra)\s+)?{_EN_UNIT}\b", re.IGNORECASE),
    re.compile(rf"{_HAN_NUMBER}+\s*{_HAN_UNIT}"),
    re.compile(rf"\d+\s*{_TA_UNIT}"),
]

_INSTRUCTION = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bskip(?:ping|ped)?\b",
        r"\bdoubl(?:e|ing)\b",
        r"\btwice\s+(?:as\s+much|the)\b",
        r"\bstop(?:ping)?\s+(?:taking|your|the|it|this|that)\b",
        r"\b(?:don'?t|do\s+not|no\s+need\s+(?:to\s+)?)\s*(?:take|eat|swallow)\b",
        r"\bcan\s+(?:skip|miss|leave)\b",
        r"\btake\s+(?:more|less|another|extra|fewer)\b",
        r"\b(?:increase|decrease|reduce|lower|raise|change|adjust|halve|cut)\s+(?:your|the|its)?\s*(?:dose|dosage|amount|medicine|medication|tablets?|pills?)\b",
        r"\bcut\s+(?:it|the\s+tablet|the\s+pill)\s+in\s+half\b",
        r"\binstead\s+of\s+(?:your|the)\s+(?:medicine|medication|tablets?|pills?)\b",
        # Cantonese / Hokkien / Mandarin, Han characters
        r"加倍|雙倍|双倍|停藥|停药|跳過|跳过|唔使食|唔駛食|唔好食|不用吃|不要吃|別吃|别吃|免食|莫食|毋免食|多食|少食|多吃|少吃|加食|減藥|减药|加藥|加药",
        # Tamil: stop, double, skip/avoid, don't take, more/less
        r"நிறுத்த|இரட்டை|தவிர்|சாப்பிட\s*வேண்டாம்|எடுக்க\s*வேண்டாம்|அதிகமாக|குறைவாக",
    )
]


def dose_or_instruction(text: str) -> str | None:
    """Why `text` may not be spoken to a resident, or None if it may.
    Returns "dose" (it states an amount) or "instruction" (it tells
    someone to change, skip, double or stop a medicine)."""
    if not text:
        return None
    for pattern in _DOSE:
        if pattern.search(text):
            return "dose"
    for pattern in _INSTRUCTION:
        if pattern.search(text):
            return "instruction"
    return None
