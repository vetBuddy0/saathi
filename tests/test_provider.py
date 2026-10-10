"""voice/engine/provider.py: which service hears her and replies."""

import subprocess
import sys
from pathlib import Path

import pytest

from saathi.voice.engine.provider import (
    CONNECT_TIMEOUT_SECONDS,
    GROQ_LLM_MODEL,
    GROQ_STT_MODEL,
    MAX_RETRIES,
    OPENAI_LLM_MODEL,
    OPENAI_STT_MODEL,
    REQUEST_TIMEOUT_SECONDS,
    AIProvider,
    ProviderUnavailable,
    choose_client_kind,
    choose_provider_name,
    missing_key,
    provider_from_env,
)
from saathi.voice.engine.rest_client import GROQ_BASE_URL, OPENAI_BASE_URL, RestChatClient

FAKE = object()
ROOT = Path(__file__).resolve().parent.parent


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


# -- which client: the SDK, or rest_client.py (2026-10-08) -----------------


def test_sdk_is_the_default_where_it_imports():
    assert type(provider_from_env({"GROQ_API_KEY": "y"}).client).__name__ == "Groq"
    assert type(provider_from_env({"OPENAI_API_KEY": "x"}).client).__name__ == "OpenAI"


def test_rest_client_when_asked_for():
    p = provider_from_env({"OPENAI_API_KEY": "x", "SAATHI_AI_CLIENT": "rest"})
    assert isinstance(p.client, RestChatClient)
    assert (p.name, p.client.base_url) == ("openai", OPENAI_BASE_URL)
    p = provider_from_env({"GROQ_API_KEY": "y", "SAATHI_AI_CLIENT": "rest"})
    assert isinstance(p.client, RestChatClient)
    assert (p.name, p.client.base_url) == ("groq", GROQ_BASE_URL)
    assert choose_client_kind({"SAATHI_AI_CLIENT": " REST "}) == "rest"


def test_rest_client_when_the_sdk_does_not_import(monkeypatch):
    # The phone: `import openai` / `import groq` raise ImportError.
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setitem(sys.modules, "groq", None)
    p = provider_from_env({"OPENAI_API_KEY": "x"})
    assert isinstance(p.client, RestChatClient) and p.client.base_url == OPENAI_BASE_URL
    p = provider_from_env({"GROQ_API_KEY": "y"})
    assert isinstance(p.client, RestChatClient) and p.client.base_url == GROQ_BASE_URL


def test_sdk_forced_fails_loudly_when_it_does_not_import(monkeypatch):
    monkeypatch.setitem(sys.modules, "groq", None)
    with pytest.raises(ProviderUnavailable, match="SAATHI_AI_CLIENT=sdk"):
        provider_from_env({"GROQ_API_KEY": "y", "SAATHI_AI_CLIENT": "sdk"})


def test_unknown_client_kind_is_refused_and_reported_at_startup():
    assert choose_client_kind({}) is None
    with pytest.raises(ProviderUnavailable):
        choose_client_kind({"SAATHI_AI_CLIENT": "curl"})
    assert "SAATHI_AI_CLIENT" in missing_key({"OPENAI_API_KEY": "x", "SAATHI_AI_CLIENT": "curl"})
    with pytest.raises(ProviderUnavailable):
        provider_from_env({"OPENAI_API_KEY": "x", "SAATHI_AI_CLIENT": "curl"})


def test_the_rest_client_is_built_with_the_sdk_clients_deadline_and_retry():
    # One policy for both clients, so a stalled request on the phone
    # holds the turn no longer than on the laptop (found in review: the
    # REST client had a 30 s single attempt of its own).
    assert (CONNECT_TIMEOUT_SECONDS, REQUEST_TIMEOUT_SECONDS, MAX_RETRIES) == (5.0, 20.0, 1)
    for env in ({"OPENAI_API_KEY": "x"}, {"GROQ_API_KEY": "y"}):
        rest = provider_from_env({**env, "SAATHI_AI_CLIENT": "rest"}).client
        assert isinstance(rest, RestChatClient)
        assert (rest.timeout, rest.connect_timeout, rest.retries) == (
            REQUEST_TIMEOUT_SECONDS,
            CONNECT_TIMEOUT_SECONDS,
            MAX_RETRIES,
        )


def test_the_sdk_client_is_built_with_the_same_numbers():
    pytest.importorskip("httpx")
    pytest.importorskip("openai")
    pytest.importorskip("groq")
    for env in ({"OPENAI_API_KEY": "x"}, {"GROQ_API_KEY": "y"}):
        sdk = provider_from_env({**env, "SAATHI_AI_CLIENT": "sdk"}).client
        assert sdk.max_retries == MAX_RETRIES
        deadline = sdk.timeout  # an httpx.Timeout: connect apart, the rest the one number
        assert (deadline.connect, deadline.read, deadline.write) == (
            CONNECT_TIMEOUT_SECONDS,
            REQUEST_TIMEOUT_SECONDS,
            REQUEST_TIMEOUT_SECONDS,
        )


def test_an_injected_client_is_used_whatever_the_client_kind():
    p = provider_from_env({"OPENAI_API_KEY": "x", "SAATHI_AI_CLIENT": "rest"}, client=FAKE)
    assert p.client is FAKE


def test_the_engine_imports_and_builds_a_provider_with_no_sdk_at_all():
    """The phone's state, in a fresh interpreter: no openai, groq, httpx
    or pydantic importable. Every module that reads a client must still
    import, and the provider must come up on the REST client. A
    module-level SDK import anywhere on this path fails here first."""
    script = """
import sys
for name in ("openai", "groq", "httpx", "pydantic", "pydantic_core"):
    sys.modules[name] = None
from saathi.voice.engine import cascade  # noqa: F401
from saathi.identity import digest, reflect  # noqa: F401
from saathi.initiative import phrase  # noqa: F401
from saathi.voice.engine.provider import provider_from_env
provider = provider_from_env({"OPENAI_API_KEY": "k"})
print(type(provider.client).__name__, provider.client.base_url)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"RestChatClient {OPENAI_BASE_URL}"
