"""Which country a number is in, and whether it is complete — plus the
one rule the brief is strictest about: an inferred country code is
always *said*, never silently applied.

Exists apart from `numbers.py` because "which digits did she say" and
"what number is that" fail differently. The first is a transcription
problem; the second is a locale problem ("nine one two three four five
six seven" is a complete Singapore mobile number and half an Indian
one).

Locale, in order:
1. A stored `country` preference (written when she confirms a
   read-back that used an inferred country — so it's learned, not
   configured).
2. The device's timezone (`/etc/timezone` or the `/etc/localtime`
   link), e.g. `Asia/Singapore` -> SG. This is where the device *is*.
3. Her language preference, only where it points at one country
   (hindi -> IN). English, Chinese and Bengali each span several
   countries and infer nothing.
If none gives an answer, the country is *missing* and she is asked.

Contested: language first lost. A Mandarin speaker in Singapore is the
common case this product is built around; language says who she is,
timezone says where the phone numbers around her are.

Contested: `phonenumbers` (Google's libphonenumber port) lost — a large
new dependency for a handful of countries. The table below is small on
purpose: calling code, national lengths, trunk prefix, and how to say
it. A country not in the table can still be saved by saying the number
with "plus"; it just can't be inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from saathi.call.numbers import SpokenNumber, spell_digits


@dataclass(frozen=True)
class Country:
    iso: str
    code: str  # calling code, no plus
    lengths: tuple[int, ...]  # national significant number lengths
    trunk: str | None  # national trunk prefix dropped in international form
    spoken: str  # "a Singapore number"


COUNTRIES: dict[str, Country] = {
    c.iso: c
    for c in (
        Country("SG", "65", (8,), None, "a Singapore number"),
        Country("US", "1", (10,), None, "a US number"),
        Country("IN", "91", (10,), "0", "an Indian number"),
        Country("GB", "44", (10,), "0", "a UK number"),
        Country("CN", "86", (11,), "0", "a China number"),
        Country("MY", "60", (9, 10), "0", "a Malaysia number"),
        Country("AU", "61", (9,), "0", "an Australian number"),
        Country("BD", "880", (10,), "0", "a Bangladesh number"),
        Country("HK", "852", (8,), None, "a Hong Kong number"),
    )
}
_BY_CODE = {c.code: c for c in COUNTRIES.values()}

TIMEZONE_COUNTRY = {
    "Asia/Singapore": "SG",
    "Asia/Kolkata": "IN",
    "Asia/Calcutta": "IN",
    "Asia/Kuala_Lumpur": "MY",
    "Asia/Shanghai": "CN",
    "Asia/Hong_Kong": "HK",
    "Asia/Dhaka": "BD",
    "Europe/London": "GB",
    "Australia/Sydney": "AU",
    "Australia/Melbourne": "AU",
    "America/New_York": "US",
    "America/Chicago": "US",
    "America/Denver": "US",
    "America/Los_Angeles": "US",
}
LANGUAGE_COUNTRY = {"hindi": "IN"}  # only languages that point at one country


def system_timezone() -> str | None:
    try:
        name = Path("/etc/timezone").read_text().strip()
        if name:
            return name
    except OSError:
        pass
    try:
        target = str(Path("/etc/localtime").resolve())
    except OSError:
        return None
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else None


def infer_country(
    stored: str | None,
    timezone: Callable[[], str | None] = system_timezone,
    language: str | None = None,
) -> tuple[str | None, str]:
    """(iso or None, where it came from: "stored" | "timezone" | "language" | "")."""
    if stored in COUNTRIES:
        return stored, "stored"
    tz_country = TIMEZONE_COUNTRY.get(timezone() or "")
    if tz_country:
        return tz_country, "timezone"
    if language in LANGUAGE_COUNTRY:
        return LANGUAGE_COUNTRY[language], "language"
    return None, ""


@dataclass(frozen=True)
class Resolved:
    e164: str | None  # E.164, e.g. "+65" followed by 8 digits, when complete
    country: str | None
    country_inferred: bool  # True -> must be said aloud before saving
    missing: tuple[str, ...]  # subset of: number, country, more_digits, check_number

    @property
    def complete(self) -> bool:
        return self.e164 is not None and not self.missing


def resolve(number: SpokenNumber, locale_country: str | None) -> Resolved:
    digits = number.digits
    if not digits:
        return Resolved(None, None, False, ("number",))
    if number.international:
        for size in (3, 2, 1):
            country = _BY_CODE.get(digits[:size])
            if country is None:
                continue
            national = digits[size:]
            if len(national) in country.lengths:
                return Resolved("+" + digits, country.iso, False, ())
            if len(national) < min(country.lengths):
                return Resolved(None, country.iso, False, ("more_digits",))
            return Resolved(None, country.iso, False, ("check_number",))
        # Unknown code: accept any plausible E.164 length, never infer.
        if 8 <= len(digits) <= 15:
            return Resolved("+" + digits, None, False, ())
        return Resolved(None, None, False, ("check_number",))

    if locale_country not in COUNTRIES:
        return Resolved(None, None, False, ("country",))
    country = COUNTRIES[locale_country]
    national = digits
    if country.trunk and national.startswith(country.trunk):
        national = national[len(country.trunk):]
    if len(national) in country.lengths:
        return Resolved(f"+{country.code}{national}", country.iso, True, ())
    # She said the country code without "plus": "six five nine one ..."
    if digits.startswith(country.code) and len(digits) - len(country.code) in country.lengths:
        return Resolved("+" + digits, country.iso, False, ())
    if len(national) < min(country.lengths):
        return Resolved(None, country.iso, True, ("more_digits",))
    return Resolved(None, country.iso, True, ("check_number",))


def group_for_display(e164: str) -> str:
    """E.164 -> "+65 9123 4567"; groups of four from the left of
    the national part, a final short group kept on the end."""
    digits = e164.lstrip("+")
    for size in (3, 2, 1):
        if digits[:size] in _BY_CODE:
            code, national = digits[:size], digits[size:]
            break
    else:
        code, national = "", digits
    groups = [national[i : i + 4] for i in range(0, len(national), 4)]
    if len(groups) > 1 and len(groups[-1]) == 1:
        groups[-2:] = [groups[-2][:3], groups[-2][3] + groups[-1]]
    return ("+" + code + " " if code else "+") + " ".join(groups)


def spoken_readback(e164: str) -> str:
    """Digit by digit, in the same groups as the card, commas as pauses."""
    parts = group_for_display(e164).lstrip("+").split(" ")
    return "plus " + ", ".join(spell_digits(part) for part in parts)


def country_sentence(resolved: Resolved) -> str:
    """The sentence that makes an inferred country code audible."""
    if not resolved.country_inferred or resolved.country is None:
        return ""
    country = COUNTRIES[resolved.country]
    return f"That's {country.spoken}, plus {spell_digits(country.code)}."
