"""voice/tts/voices.py -- the paired-voice list is code, and these pin
its shape the way test_language.py pins SUPPORTED_LANGUAGES: six at
most, every entry a real backend, exactly one offline fallback and it
is last, the default is Chirp, and every supported language has both a
model name and a preview sentence. A list that drifts from these rules
is a panel that lies.
"""

from saathi.voice.language import SUPPORTED_LANGUAGES
from saathi.voice.tts.registry import DEFAULT_BACKEND_ID, default_backends
from saathi.voice.tts.voices import (
    DEFAULT_VOICE_ID,
    MAX_VOICES,
    PREVIEW_SENTENCES,
    VOICES,
    default_voice_for_backend,
    voice_by_id,
    voice_from_legacy_backend,
)


def test_at_most_six_voices_a_picker_not_a_catalogue():
    assert MAX_VOICES == 6
    assert 1 <= len(VOICES) <= MAX_VOICES


def test_ids_and_names_are_unique():
    assert len({v.id for v in VOICES}) == len(VOICES)
    assert len({v.name for v in VOICES}) == len(VOICES)


def test_every_voice_is_on_a_backend_the_registry_knows():
    registry = default_backends()
    for voice in VOICES:
        assert voice.backend_id in registry, voice


def test_names_are_plain_words_not_model_names():
    for voice in VOICES:
        assert voice.name.isalpha() and voice.name[0].isupper(), voice.name
        assert "-" not in voice.name and "Chirp" not in voice.name


def test_exactly_one_offline_fallback_and_it_is_piper_and_last():
    offline = [v for v in VOICES if v.offline]
    assert len(offline) == 1
    assert offline[0].backend_id == DEFAULT_BACKEND_ID
    assert offline[0].speaker is None
    assert VOICES[-1] is offline[0]


def test_the_default_voice_is_chirp3_hd_not_piper():
    default = voice_by_id(DEFAULT_VOICE_ID)
    assert default is not None
    assert default.backend_id == "google-chirp3-hd"
    assert default.offline is False
    assert default.speaker == "Sulafat"  # the speaker compare.py shipped as default


def test_every_chirp_voice_is_the_same_speaker_in_every_language():
    # The whole reason for pairs: one character across languages.
    for voice in VOICES:
        if voice.speaker is None:
            continue
        for language in SUPPORTED_LANGUAGES:
            assert voice.model_name(language).endswith(f"-Chirp3-HD-{voice.speaker}")


def test_model_names_exist_for_english_and_mandarin_on_every_voice():
    for voice in VOICES:
        assert voice.model_name("english")
        assert voice.model_name("chinese")
    piper = VOICES[-1]
    assert piper.model_name("english") == SUPPORTED_LANGUAGES["english"]
    assert voice_by_id("warm").model_name("chinese") == "cmn-CN-Chirp3-HD-Sulafat"


def test_a_preview_sentence_exists_for_every_supported_language():
    assert set(PREVIEW_SENTENCES) == set(SUPPORTED_LANGUAGES)
    for sentence in PREVIEW_SENTENCES.values():
        assert sentence.strip()
        assert "Sure" not in sentence and "Certainly" not in sentence  # SPEC.md, Tone


def test_voice_by_id_returns_none_for_unknown_or_missing():
    assert voice_by_id("warm") is VOICES[0]
    assert voice_by_id("nope") is None
    assert voice_by_id(None) is None


def test_default_voice_for_backend_is_the_first_pair_or_none():
    assert default_voice_for_backend("google-chirp3-hd").id == DEFAULT_VOICE_ID
    assert default_voice_for_backend("piper").id == "plain"
    # No Neural2 Mandarin voice exists, so Neural2 can't be a pair.
    assert default_voice_for_backend("google-neural2") is None


def test_legacy_tts_backend_preference_maps_to_a_pair():
    assert voice_from_legacy_backend("google-chirp3-hd") == "warm"
    assert voice_from_legacy_backend("piper") == "plain"
    assert voice_from_legacy_backend("google-neural2") == DEFAULT_VOICE_ID
    assert voice_from_legacy_backend(None) == DEFAULT_VOICE_ID
