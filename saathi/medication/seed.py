"""Eleven fake residents on realistic daily rounds, for the mock eMAR --
ten with a schedule only, and Lee Kim Tan (bed 6), the test resident
with a full companion profile too (identity/test_profile.py).

Why it exists: Phases 2-5 need a ward to run against before the home's
eMAR vendor is known. Names are invented. Medications, doses and round
times are typical of a nursing home, not anyone's record.

The purpose sentences are plain-language and say what a medicine is
*for*, never how much or how to take it -- they pass guard.py like any
import. The Cantonese, Hokkien (Han characters) and Tamil sentences are
a first draft and need a native speaker's review before anyone hears
them (TODO.md).
"""

from __future__ import annotations

from datetime import time

from saathi.medication.emar import MockEMARAdapter
from saathi.medication.models import Resident

MORNING = (time(8, 0), time(9, 0))
AFTERNOON = (time(13, 30), time(14, 30))  # "the 2pm dose"
EVENING = (time(20, 0), time(21, 0))

PURPOSES: dict[str, dict[str, str]] = {
    "blood_sugar": {
        "singlish": "This one helps keep your blood sugar steady.",
        "cantonese": "呢隻藥係幫你穩定血糖嘅。",
        "hokkien": "這款藥仔是欲予你的血糖較穩定。",
        "tamil": "இந்த மருந்து உங்கள் இரத்தச் சர்க்கரையைச் சீராக வைக்க உதவுகிறது.",
    },
    "blood_pressure": {
        "singlish": "This one is for your blood pressure, so it won't go too high.",
        "cantonese": "呢隻藥係幫你控制血壓嘅。",
        "hokkien": "這款藥仔是欲顧你的血壓。",
        "tamil": "இந்த மருந்து உங்கள் இரத்த அழுத்தத்தைக் கட்டுப்படுத்த உதவுகிறது.",
    },
    "cholesterol": {
        "singlish": "This one keeps your cholesterol down and helps protect your heart.",
        "cantonese": "呢隻藥係幫你降膽固醇，保護心臟。",
        "hokkien": "這款藥仔是欲降膽固醇，保護你的心臟。",
        "tamil": "இந்த மருந்து கொலஸ்ட்ராலைக் குறைத்து உங்கள் இதயத்தைப் பாதுகாக்க உதவுகிறது.",
    },
    "stomach": {
        "singlish": "This one protects your stomach from too much acid.",
        "cantonese": "呢隻藥係保護你個胃嘅。",
        "hokkien": "這款藥仔是欲保護你的胃。",
        "tamil": "இந்த மருந்து உங்கள் வயிற்றை அமிலத்திலிருந்து பாதுகாக்கிறது.",
    },
    "pain": {
        "singlish": "This one is for pain, and it brings down fever too.",
        "cantonese": "呢隻藥係止痛同退燒嘅。",
        "hokkien": "這款藥仔是止疼佮退燒的。",
        "tamil": "இந்த மருந்து வலியையும் காய்ச்சலையும் குறைக்கிறது.",
    },
    "memory": {
        "singlish": "This one helps with your memory.",
        "cantonese": "呢隻藥係幫你記性嘅。",
        "hokkien": "這款藥仔是欲鬥相共你的記持。",
        "tamil": "இந்த மருந்து உங்கள் ஞாபக சக்திக்கு உதவுகிறது.",
    },
    "kidneys": {
        "singlish": "This one helps your blood pressure and protects your kidneys.",
        "cantonese": "呢隻藥係幫你控制血壓，保護腎臟。",
        "hokkien": "這款藥仔是欲顧你的血壓，閣保護腰子。",
        "tamil": "இந்த மருந்து இரத்த அழுத்தத்தைக் கட்டுப்படுத்தி உங்கள் சிறுநீரகங்களைப் பாதுகாக்கிறது.",
    },
    "clots": {
        "singlish": "This one stops your blood from clotting, to protect your heart.",
        "cantonese": "呢隻藥係防止血栓，保護心臟。",
        "hokkien": "這款藥仔是欲防止血栓，保護你的心臟。",
        "tamil": "இந்த மருந்து இரத்தம் உறைவதைத் தடுத்து உங்கள் இதயத்தைப் பாதுகாக்கிறது.",
    },
    "thyroid": {
        "singlish": "This one is for your thyroid, so you have more energy.",
        "cantonese": "呢隻藥係幫你甲狀腺，等你有精神啲。",
        "hokkien": "這款藥仔是欲顧你的甲狀腺，予你較有元氣。",
        "tamil": "இந்த மருந்து உங்கள் தைராய்டுக்கு உதவி உங்களுக்குச் சக்தி தருகிறது.",
    },
    "gout": {
        "singlish": "This one helps keep gout from flaring up in your joints.",
        "cantonese": "呢隻藥係預防痛風發作嘅。",
        "hokkien": "這款藥仔是欲防止痛風發作。",
        "tamil": "இந்த மருந்து மூட்டுகளில் கீல்வாதம் வராமல் தடுக்கிறது.",
    },
    "urine": {
        "singlish": "This one makes it easier for you to pass urine.",
        "cantonese": "呢隻藥係幫你小便順暢啲。",
        "hokkien": "這款藥仔是欲予你放尿較順。",
        "tamil": "இந்த மருந்து சிறுநீர் கழிப்பதை எளிதாக்குகிறது.",
    },
    "bones": {
        "singlish": "This one keeps your bones strong.",
        "cantonese": "呢隻藥係令你啲骨頭強壯啲。",
        "hokkien": "這款藥仔是欲予你的骨頭較勇。",
        "tamil": "இந்த மருந்து உங்கள் எலும்புகளை வலுவாக வைக்கிறது.",
    },
}

MEDICATIONS = {  # name -> (dose as the eMAR writes it, purpose key)
    "Metformin": ("500 mg", "blood_sugar"),
    "Amlodipine": ("5 mg", "blood_pressure"),
    "Atorvastatin": ("20 mg", "cholesterol"),
    "Omeprazole": ("20 mg", "stomach"),
    "Paracetamol": ("1 g", "pain"),
    "Donepezil": ("5 mg", "memory"),
    "Losartan": ("50 mg", "kidneys"),
    "Aspirin": ("100 mg", "clots"),
    "Levothyroxine": ("50 mcg", "thyroid"),
    "Vitamin D3": ("1000 IU", "bones"),
    "Allopurinol": ("100 mg", "gout"),
    "Tamsulosin": ("0.4 mg", "urine"),
}

RESIDENTS = [
    Resident("r01", "1", "Tan Ah Kow", "hokkien"),
    Resident("r02", "2", "Wong Mei Ling", "cantonese"),
    Resident("r03", "3", "Lakshmi Ramasamy", "tamil"),
    Resident("r04", "5", "Lim Bee Hoon", "hokkien"),
    Resident("r05", "7", "Chan Siu Fong", "cantonese"),
    Resident("r06", "8", "Muthu Krishnan", "tamil"),
    Resident("r07", "9", "Mary Goh", "singlish"),
    Resident("r08", "10", "Ong Teck Seng", "hokkien"),
    Resident("r09", "12", "Leung Kam Wah", "cantonese"),
    Resident("r10", "14", "Ahmad Rahim", "singlish"),
    # The test resident with a full profile (identity/test_profile.py).
    Resident("r11", "6", "Lee Kim Tan", "hokkien"),
]

ROUNDS: dict[str, list[tuple[str, tuple[time, time]]]] = {
    "r01": [("Metformin", MORNING), ("Amlodipine", MORNING), ("Metformin", EVENING)],
    "r02": [("Levothyroxine", MORNING), ("Paracetamol", AFTERNOON)],
    "r03": [("Losartan", MORNING), ("Atorvastatin", EVENING), ("Vitamin D3", MORNING)],
    "r04": [("Donepezil", EVENING), ("Omeprazole", MORNING)],
    "r05": [("Aspirin", MORNING), ("Atorvastatin", EVENING), ("Amlodipine", MORNING)],
    "r06": [("Metformin", MORNING), ("Metformin", EVENING), ("Losartan", MORNING)],
    "r07": [("Paracetamol", MORNING), ("Paracetamol", AFTERNOON), ("Vitamin D3", MORNING)],
    "r08": [("Amlodipine", MORNING), ("Donepezil", EVENING)],
    "r09": [("Omeprazole", MORNING), ("Paracetamol", AFTERNOON), ("Atorvastatin", EVENING)],
    "r10": [("Aspirin", MORNING), ("Levothyroxine", MORNING), ("Metformin", EVENING)],
    "r11": [
        ("Metformin", MORNING), ("Amlodipine", MORNING), ("Aspirin", MORNING),
        ("Omeprazole", MORNING), ("Allopurinol", MORNING), ("Vitamin D3", MORNING),
        ("Paracetamol", AFTERNOON),
        ("Metformin", EVENING), ("Atorvastatin", EVENING), ("Tamsulosin", EVENING),
    ],
}


def seed(emar: MockEMARAdapter) -> int:
    """Fill the mock eMAR; returns how many daily doses it now plans.
    Re-running replaces the same rows, never duplicates them."""
    count = 0
    for resident in RESIDENTS:
        emar.add_resident(resident)
        for n, (medication, (start, end)) in enumerate(ROUNDS[resident.id], start=1):
            dose, purpose = MEDICATIONS[medication]
            emar.add_plan(f"{resident.id}-{n}", resident.id, medication, dose, start, end,
                          PURPOSES[purpose])
            count += 1
    return count
