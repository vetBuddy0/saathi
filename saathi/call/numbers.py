"""Turning a spoken phone number into digits: "nine one double seven, oh
four... three two" -> "9177043 2"-without-the-space.

Exists as a pure function because every hard case is a sentence, and a
sentence is a unit test: "double seven", "triple oh", "oh" for zero,
"nine one" and "ninety-one" (both 9-1; "ninety" alone is 9-0), teens,
"eight hundred", pauses Whisper renders as commas or ellipses, fillers
("um", "then", "and"), and numerals Whisper sometimes writes instead of
words. Nothing here knows about countries; `phone.py` decides whether
the digits are a complete number and where.

Contested: a grammar/NLU library lost — a new dependency for one
closed-vocabulary problem. So did asking the model to normalise the
number: a model "correcting" a digit is exactly the silent wrong digit
the read-back card exists to prevent, so the model passes her words
through as spoken and this function does the arithmetic.

Confidence is the share of non-filler words that were understood; any
unknown word also lands in `unknown` so the read-back is never skipped
and the caller can say which part it didn't catch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_UNITS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "naught": "0", "nil": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
# Deliberately absent: homophones ("to"/"too" for 2, "for" for 4, "won",
# "ate"). "The number for Priya is ..." would silently gain a 4. They
# land in `unknown` instead, lowering confidence, and the read-back
# card catches the rest.
_TEENS = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
}
_TENS = {
    "twenty": "2", "thirty": "3", "forty": "4", "fourty": "4", "fifty": "5",
    "sixty": "6", "seventy": "7", "eighty": "8", "ninety": "9",
}
_REPEAT = {"double": 2, "triple": 3, "treble": 3}
_FILLERS = {
    "um", "umm", "uh", "er", "erm", "hmm", "and", "then", "is", "it", "its", "it's",
    "the", "number", "my", "like", "okay", "ok", "so", "a", "dash", "space", "wait",
    "sorry", "that's", "thats", "code", "country", "phone", "her", "his", "their", "for",
}
_TOKEN_RE = re.compile(r"\+|[0-9]+|[a-z']+")


@dataclass(frozen=True)
class SpokenNumber:
    digits: str  # only 0-9
    international: bool  # said "plus" / "+" / a leading "zero zero" before any digit
    confidence: float  # 0..1: share of content words understood
    unknown: tuple[str, ...] = field(default=())

    @property
    def missing(self) -> tuple[str, ...]:
        return ("number",) if not self.digits else ()


def parse_spoken_number(text: str) -> SpokenNumber:
    tokens = _TOKEN_RE.findall((text or "").lower().replace("-", " "))
    digits: list[str] = []
    international = False
    unknown: list[str] = []
    understood = 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if token in ("+", "plus"):
            if not digits:
                international = True
            understood += 1
        elif token.isdigit():
            digits.append(token)
            understood += 1
        elif token in _REPEAT and nxt is not None and (nxt in _UNITS or nxt.isdigit()):
            unit = _UNITS.get(nxt, nxt)
            digits.append(unit * _REPEAT[token])
            understood += 2
            i += 1
        elif token in _UNITS:
            digits.append(_UNITS[token])
            understood += 1
        elif token in _TEENS:
            digits.append(_TEENS[token])
            understood += 1
        elif token in _TENS:
            if nxt is not None and nxt in _UNITS and _UNITS[nxt] != "0":
                digits.append(_TENS[token] + _UNITS[nxt])
                understood += 2
                i += 1
            else:
                digits.append(_TENS[token] + "0")
                understood += 1
        elif token == "hundred" and digits:
            digits.append("00")
            understood += 1
        elif token == "thousand" and digits:
            digits.append("000")
            understood += 1
        elif token in _FILLERS:
            pass
        else:
            unknown.append(token)
        i += 1

    joined = "".join(digits)
    if not international and joined.startswith("00") and len(joined) > 4:
        # "zero zero six five ..." is the international prefix, not two zeros.
        international = True
        joined = joined[2:]
    total = understood + len(unknown)
    confidence = understood / total if total else 0.0
    return SpokenNumber(joined, international, confidence, tuple(unknown))


def spell_digits(digits: str) -> str:
    """Digit by digit, for speech: "6591" -> "six five nine one"."""
    words = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
    return " ".join(words[int(c)] for c in digits if c.isdigit())
