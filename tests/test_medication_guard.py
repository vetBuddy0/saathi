"""medication/guard.py: sentences that state a dose or tell someone to
change, skip, double or stop a medicine are caught, in every medication
language; plain purpose sentences pass. Erring towards blocking is
intended (a blocked sentence becomes "the nurse will explain")."""

import pytest

from saathi.medication.guard import dose_or_instruction
from saathi.medication.seed import PURPOSES

DOSES = [
    "Take 2 tablets for the pain.",
    "It's 500 mg, morning and night.",
    "This is 1.5mg.",
    "Two pills after food.",
    "half a tablet is enough",
    "50 mcg of levothyroxine",
    "One more dose tonight.",
    "食兩粒就得。",
    "每日一片。",
    "呢隻係五百毫克。",
    "இரண்டு வேளை, 2 மாத்திரை.",
    "500 மி.கி மருந்து",
]

INSTRUCTIONS = [
    "You can skip it today.",
    "Just double it tomorrow.",
    "Stop taking it if you feel sick.",
    "Don't take it tonight.",
    "No need to take the pill lah.",
    "Take more if the pain is bad.",
    "You could reduce the dose.",
    "唔使食啦。",
    "今晚免食。",
    "可以加倍。",
    "停藥先。",
    "இந்த மருந்தை நிறுத்தலாம்.",
    "இன்று சாப்பிட வேண்டாம்.",
]


@pytest.mark.parametrize("text", DOSES)
def test_doses_are_caught(text):
    assert dose_or_instruction(text) == "dose"


@pytest.mark.parametrize("text", INSTRUCTIONS)
def test_instructions_are_caught(text):
    assert dose_or_instruction(text) is not None


@pytest.mark.parametrize(
    "text", [t for purpose in PURPOSES.values() for t in purpose.values()]
)
def test_every_seeded_purpose_sentence_passes(text):
    assert dose_or_instruction(text) is None


@pytest.mark.parametrize("text", [
    "He loves Teresa Teng's songs.",
    "Bed 6, Ward 2.",
    "He drove a taxi for 35 years.",
    "Uncle Lee is 78.",
])
def test_ordinary_sentences_pass(text):
    assert dose_or_instruction(text) is None


def test_empty_text_passes():
    assert dose_or_instruction("") is None
