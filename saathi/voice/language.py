"""One source of truth for which languages Saathi can actually speak.

SPEC.md's v0.1 scope names six languages (English, Mandarin, Hindi,
Bengali, Tamil, Malay). This module is narrower on purpose: it lists only
the languages with a real voice behind them *right now*. Verified against
Piper's own voice list (`python3 -m piper.download_voices`), not assumed —
it ships voices for English (`en_*`), Mandarin (`zh_CN-*`), Hindi
(`hi_IN-*`) and Bengali (`bn_BD-*`), and none at all for Tamil or Malay.
Google Cloud TTS covers all six (confirmed: `cmn-CN`, `hi-IN`, `bn-IN`,
`ta-IN` via Chirp 3 HD, `ms-MY` since 2021) and is the candidate for
closing that gap — evaluated, not yet wired in (see the commit this
landed in). Tamil and Malay stay out of `SUPPORTED_LANGUAGES` until a
backend actually covers them: detecting a language and having nothing to
say it in fails worse than a wrong detection, because it fails silently,
mid-reply, with no voice at all.

Keys are Groq Whisper's own detected-language names, lowercased (its
`verbose_json` response returns `"English"`, `"Chinese"`, ... — confirmed
against a real transcription, not the ISO codes some other STT APIs use).
Using Whisper's own vocabulary as the key means there is no separate
translation table between "what detection said" and "what the voice table
knows" to drift out of sync — that gap is exactly what let a bad
detection (the Portuguese reply, see core.py's module docstring for the
capture-overlap bug behind it) turn into a spoken reply nobody signed off
on.
"""

from __future__ import annotations

# Whisper's detected-language name (lowercased) -> Piper voice name.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "english": "en_US-amy-medium",
    "chinese": "zh_CN-huayan-medium",
    "hindi": "hi_IN-pratham-medium",
    "bengali": "bn_BD-google-medium",
}

DEFAULT_LANGUAGE = "english"


def resolve_language(detected: str | None, last_used: str) -> str:
    """`detected` is Whisper's `verbose_json` `language` field, lowercased
    (or `None`/anything else if that wasn't available). Returns `detected`
    only if we can actually speak it; otherwise falls back to `last_used`
    — never a language with no voice, and never a raw detection trusted
    on its own. If `last_used` itself somehow isn't in the supported set,
    falls back to `DEFAULT_LANGUAGE` rather than propagating the problem.
    """
    if detected in SUPPORTED_LANGUAGES:
        return detected
    if last_used in SUPPORTED_LANGUAGES:
        return last_used
    return DEFAULT_LANGUAGE
