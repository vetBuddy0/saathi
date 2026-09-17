from saathi.voice.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, resolve_language


def test_supported_languages_match_the_verified_piper_set():
    # Verified against `python3 -m piper.download_voices`'s own listing:
    # Piper ships voices for these four and none at all for Tamil or
    # Malay. This test is the "enforced in code" part — a language added
    # here without also being real is exactly the bug this guards against.
    assert set(SUPPORTED_LANGUAGES) == {"english", "chinese", "hindi", "bengali"}
    assert "tamil" not in SUPPORTED_LANGUAGES
    assert "malay" not in SUPPORTED_LANGUAGES


def test_default_language_is_supported():
    assert DEFAULT_LANGUAGE in SUPPORTED_LANGUAGES


def test_resolve_language_accepts_a_supported_detection():
    assert resolve_language("chinese", "english") == "chinese"


def test_resolve_language_falls_back_to_last_used_on_unsupported_detection():
    # The Portuguese-reply bug, reduced to its cause: a noisy detection
    # outside the supported set must not be trusted.
    assert resolve_language("portuguese", "english") == "english"


def test_resolve_language_falls_back_to_last_used_on_no_detection():
    assert resolve_language(None, "hindi") == "hindi"


def test_resolve_language_falls_back_to_default_when_last_used_is_also_unsupported():
    assert resolve_language("portuguese", "tamil") == DEFAULT_LANGUAGE


def test_resolve_language_rejects_tamil_and_malay_even_though_spec_names_them():
    # SPEC.md's v0.1 scope names six languages; the code allowlist is
    # narrower until a TTS backend covers the other two. A detection of
    # either must not be trusted just because it's an intended language.
    assert resolve_language("tamil", "english") == "english"
    assert resolve_language("malay", "english") == "english"


# Mixed-language utterances (code-switching within one turn) are a
# required test case — the pairs most likely to occur, per the checkpoint
# 2 language brief. Whisper's `verbose_json` reports one dominant language
# for the whole turn, so at this layer a code-switched utterance shows up
# as "the detection is one of the pair while the conversation's last
# language was the other" — both orderings, since code-switching runs
# both directions in real conversation.


def test_mixed_language_pair_english_mandarin_both_directions_are_trusted():
    assert resolve_language("english", "chinese") == "english"
    assert resolve_language("chinese", "english") == "chinese"


def test_mixed_language_pair_english_hindi_both_directions_are_trusted():
    assert resolve_language("english", "hindi") == "english"
    assert resolve_language("hindi", "english") == "hindi"


def test_mixed_language_pair_english_malay_falls_back_since_malay_has_no_voice():
    # Malay has no Piper voice (verified) and Google TTS isn't adopted
    # yet, so a code-switched utterance Whisper reports as Malay must
    # fall back rather than attempt a voice that doesn't exist.
    assert resolve_language("malay", "english") == "english"
    # The reverse can't arise: resolve_language() never returns an
    # unsupported language, so "last_used" can never actually be Malay.
