"""voice/engine/provider.py: which service hears her and replies."""

import pytest

from saathi.voice.engine.provider import (
    GROQ_LLM_MODEL,
    GROQ_STT_MODEL,
    OPENAI_LLM_MODEL,
    OPENAI_STT_MODEL,
    AIProvider,
    ProviderUnavailable,
    choose_provider_name,
    missing_key,
    provider_from_env,
)

FAKE = object()


def test_openai_wins_when_its_key_is_set():
    env = {"OPENAI_API_KEY": "x", "GROQ_API_KEY": "y"}
    assert choose_provider_name(env) == "openai"
    p = provider_from_env(env, client=FAKE)
    assert (p.name, p.llm_model, p.stt_model) == ("openai", OPENAI_LLM_MODEL, OPENAI_STT_MODEL)


def test_groq_when_only_its_key_is_set():
    p = provider_from_env({"GROQ_API_KEY": "y"}, client=FAKE)
    assert (p.name, p.llm_model, p.stt_model) == ("groq", GROQ_LLM_MODEL, GROQ_STT_MODEL)


def test_explicit_choice_and_model_overrides():
    env = {"OPENAI_API_KEY": "x", "GROQ_API_KEY": "y", "SAATHI_AI_PROVIDER": "groq",
           "SAATHI_LLM_MODEL": "m", "SAATHI_STT_MODEL": "s"}
    p = provider_from_env(env, client=FAKE)
    assert (p.name, p.llm_model, p.stt_model) == ("groq", "m", "s")


def test_unknown_explicit_provider_is_refused_not_guessed():
    with pytest.raises(ProviderUnavailable):
        choose_provider_name({"SAATHI_AI_PROVIDER": "claude"})


def test_no_key_at_all():
    assert choose_provider_name({}) is None
    assert missing_key({}) == "OPENAI_API_KEY or GROQ_API_KEY"
    with pytest.raises(ProviderUnavailable):
        provider_from_env({})


def test_missing_key_names_the_chosen_providers_key_never_a_value():
    assert missing_key({"SAATHI_AI_PROVIDER": "openai", "GROQ_API_KEY": "y"}) == "OPENAI_API_KEY"
    assert missing_key({"OPENAI_API_KEY": "secret"}) is None
    with pytest.raises(ProviderUnavailable):
        provider_from_env({"SAATHI_AI_PROVIDER": "openai"})  # no key, no client


def test_only_whisper_models_report_language():
    whisper = AIProvider("groq", FAKE, "m", "whisper-large-v3-turbo")
    assert whisper.stt_response_format == "verbose_json"
    assert AIProvider("openai", FAKE, "m", "whisper-1").stt_reports_language
    newer = AIProvider("openai", FAKE, "m", "gpt-transcribe")
    assert not newer.stt_reports_language and newer.stt_response_format == "json"


def test_reasoning_off_only_for_openai_reasoning_families():
    # Measured: gpt-5.x / gpt-6 reject function tools on chat completions
    # unless reasoning_effort is "none".
    assert AIProvider("openai", FAKE, "gpt-6-sol", "s").llm_extra == {"reasoning_effort": "none"}
    assert AIProvider("openai", FAKE, "gpt-5.6-luna", "s").llm_extra == {"reasoning_effort": "none"}
    assert AIProvider("openai", FAKE, "gpt-4.1", "s").llm_extra == {}
    assert AIProvider("groq", FAKE, "gpt-5-lookalike", "s").llm_extra == {}


# -- hearing on Groq, replying on OpenAI (2026-10-07) -----------------------


def test_with_both_keys_groq_hears_her_and_openai_covers_for_it():
    provider = provider_from_env({"OPENAI_API_KEY": "sk-x", "GROQ_API_KEY": "gsk-x"})
    assert provider.name == "openai" and provider.llm_model == OPENAI_LLM_MODEL
    assert provider.stt_model == GROQ_STT_MODEL
    assert type(provider.transcriber).__module__.startswith("groq")
    fallback_client, fallback_model = provider.stt_fallback
    assert fallback_client is provider.client and fallback_model == OPENAI_STT_MODEL


def test_the_split_can_be_turned_off():
    for extra in ({"SAATHI_STT_PROVIDER": "openai"}, {"SAATHI_STT_MODEL": "whisper-1"}):
        provider = provider_from_env({"OPENAI_API_KEY": "sk-x", "GROQ_API_KEY": "gsk-x", **extra})
        assert provider.transcriber is provider.client and provider.stt_fallback is None


def test_openai_alone_hears_and_replies():
    provider = provider_from_env({"OPENAI_API_KEY": "sk-x"})
    assert provider.stt_model == OPENAI_STT_MODEL and provider.transcriber is provider.client
