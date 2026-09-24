"""The voices she can have: one source of truth, six entries, each one a
*character* rather than a model name.

Exists because a flat list of TTS model names fails checkpoint 2's exit
condition ("recognisably the same character") the moment she switches
language: pick `en-US-Chirp3-HD-Aoede` for English and whatever for
Mandarin and she becomes a different person mid-conversation. So a
"voice" here is a pair -- the same speaker in every language we
support -- and selecting one selects all of its languages at once.
Google's Chirp3-HD is the only backend in the registry that can offer
that structurally (one speaker name applied to every language code,
DECISIONS.md 2026-09-25); Piper's voices are one person per language and
can't, but Piper is the offline fallback and is listed as exactly that,
not as a character. Neural2 is absent on purpose: Google has no Neural2
Mandarin voice (the backend uses `cmn-CN-Wavenet-A` there, a different
person from `en-US-Neural2-C`), so it cannot form a pair under the rule
above. It stays visible in the panel's backend row with its list price
so the cost comparison the user asked for is still there; it just isn't
a voice.

Six entries maximum, by decision: this is a picker for setup, not a
catalogue. The names are Google's own one-word descriptors for those
speakers (Sulafat "Warm", Achernar "Soft", Vindemiatrix "Gentle", Gacrux
"Mature", Zephyr "Bright") -- plain words a person would use, with the
model name shown underneath in small text for the operator, never for
her. All five were confirmed live to exist, female, in `en-US`, `cmn-CN`,
`hi-IN` and `bn-IN` (2026-09-25, `list_voices()` at the Singapore
endpoint). "Warm" is the default because it is the brief's word and the
speaker `compare.py` already shipped as `DEFAULT_CHIRP_SPEAKER`.

`PREVIEW_SENTENCES` live here rather than in the screen server because
they are part of what a voice *is* to the operator: nobody can choose a
voice from a name, so every selection speaks one sentence immediately,
in her current language, and that sentence has to exist for every
language in `SUPPORTED_LANGUAGES` or a preview would fail silently for a
Hindi or Bengali speaker. Same rules as the language list
(`voice/language.py`): the list is code, tests pin its shape, the panel
renders whatever is here.

The option that lost: keying the preference by backend id plus a
speaker string. That would have let a stray `tts_backend=google-neural2`
row select a backend with no pair, and put the pairing rule in the
panel's JavaScript instead of one Python constant. The old `tts_backend`
preference is still *read* (see `identity/preferences.py`'s
`read_voice_preference`, which maps it to a pair) so a device that
picked Chirp before this list existed keeps its voice.
"""

from __future__ import annotations

from dataclasses import dataclass

from saathi.voice.language import SUPPORTED_LANGUAGES
from saathi.voice.tts.google_backend import chirp3_hd_voice_name

MAX_VOICES = 6


@dataclass(frozen=True)
class VoicePair:
    id: str
    """The preference value stored in `preferences` under `VOICE_KEY`."""
    name: str
    """What a person would call it: "Warm", not "en-US-Chirp3-HD-Sulafat"."""
    backend_id: str
    """A key of `registry.default_backends()`."""
    speaker: str | None
    """The Chirp3-HD speaker name applied to every language code; `None`
    for Piper, whose voices are per language and come from
    `SUPPORTED_LANGUAGES`."""
    offline: bool
    """True only for the fallback that needs no network."""

    def model_name(self, language: str) -> str:
        """The backend's own name for this pair's voice in `language` --
        the small text under the plain name. Raises `KeyError` for a
        language outside `SUPPORTED_LANGUAGES`, same as the Piper table
        does, rather than inventing a name."""
        if self.speaker is None:
            return SUPPORTED_LANGUAGES[language]
        return chirp3_hd_voice_name(self.speaker, language)


VOICES: tuple[VoicePair, ...] = (
    VoicePair("warm", "Warm", "google-chirp3-hd", "Sulafat", offline=False),
    VoicePair("soft", "Soft", "google-chirp3-hd", "Achernar", offline=False),
    VoicePair("gentle", "Gentle", "google-chirp3-hd", "Vindemiatrix", offline=False),
    VoicePair("mature", "Mature", "google-chirp3-hd", "Gacrux", offline=False),
    VoicePair("bright", "Bright", "google-chirp3-hd", "Zephyr", offline=False),
    VoicePair("plain", "Plain", "piper", None, offline=True),
)

DEFAULT_VOICE_ID = "warm"

# One sentence per supported language, spoken the moment a voice is
# selected. Something she would actually hear, in her language, not
# "the quick brown fox" -- and nothing SPEC.md's Tone section forbids.
PREVIEW_SENTENCES: dict[str, str] = {
    "english": "Hello. I'm here whenever you'd like to talk.",
    "chinese": "你好。你想聊天的时候，我都在。",
    "hindi": "नमस्ते। जब भी आप बात करना चाहें, मैं यहीं हूँ।",
    "bengali": "নমস্কার। আপনি যখনই কথা বলতে চান, আমি এখানেই আছি।",
}


def voice_by_id(voice_id: str | None) -> VoicePair | None:
    for voice in VOICES:
        if voice.id == voice_id:
            return voice
    return None


def default_voice_for_backend(backend_id: str) -> VoicePair | None:
    """The first pair on `backend_id`, or `None` for a backend that has
    no pair (Neural2, Kokoro, Melo) -- which is how the panel knows a
    backend is not selectable as a voice."""
    for voice in VOICES:
        if voice.backend_id == backend_id:
            return voice
    return None


def voice_from_legacy_backend(tts_backend_value: str | None) -> str:
    """Maps a pre-picker `tts_backend` preference to a voice id, so a
    device that chose Chirp before voices existed keeps hearing Chirp.
    Anything without a pair falls to the default rather than to Piper:
    the old value expressed "not Piper", and the default honours that."""
    voice = default_voice_for_backend(tts_backend_value or "")
    return voice.id if voice is not None else DEFAULT_VOICE_ID
