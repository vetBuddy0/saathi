"""Which AI service hears her and thinks for her: one client, two models.

Why this exists: until 2026-09-25 the cascade was Groq-only, with the
client built inside `CascadeSession` and the two model names as module
constants. For the demo the user chose OpenAI's models for both
speech-to-text and the reply ("change later if required"), after a
live session showed two Groq problems: its on-demand tier caps output
at 1000 tokens a minute, which rejected tool turns outright, and the
model sometimes asked "which song?" instead of calling the music tool.
Both services expose the same `audio.transcriptions` and
`chat.completions` shapes, so the switch is this one small seam rather
than a second engine. Choosing between them is configuration, not
code: `SAATHI_AI_PROVIDER`, `SAATHI_LLM_MODEL`, `SAATHI_STT_MODEL`.

The option that lost: a full `VoiceSession` per provider. The pipeline
(speech gate, memory layers, tools, TTS) is identical either way;
duplicating it to swap two HTTP endpoints would be two places for
every future fix to land.

Revisiting this for cost is in TODO.md. Nothing here decides price.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Groq: the models item D's bake-off chose (see cascade.py's comments).
GROQ_LLM_MODEL = "qwen/qwen3.8-27b"
GROQ_STT_MODEL = "whisper-large-v3-turbo"

# OpenAI: defaults for the demo, chosen by measurement on the account
# (2026-09-25, DECISIONS.md), not by name. Chat: the live failure case
# -- "help me play a song by Ed Sheeran" with all six tools and a prior
# exchange -- four times each. gpt-4.1 called the music tool 4/4 at 742
# ms median, the fastest that never missed; gpt-4.1-mini, the first
# guess, called it 0/4. Transcription: gpt-transcribe, the newest, exact
# on English and Mandarin test sentences at ~870 ms (whisper-1: 1.7 s).
OPENAI_LLM_MODEL = "gpt-4.1"
OPENAI_STT_MODEL = "gpt-transcribe"

# OpenAI's reasoning families reject function tools on chat completions
# unless reasoning is off ("set reasoning_effort to 'none'" -- measured),
# and reasoning is latency a voice turn can't spare. Sent only to these.
_REASONING_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")

PROVIDERS = ("openai", "groq")


class ProviderUnavailable(RuntimeError):
    """No usable provider: the chosen one's API key is missing."""


@dataclass(frozen=True)
class AIProvider:
    name: str
    client: Any
    llm_model: str
    stt_model: str

    @property
    def stt_reports_language(self) -> bool:
        """Whisper-family models return the detected language
        (`response_format="verbose_json"`); OpenAI's newer transcribers
        accept only plain `json` and return text alone. Without it the
        cascade reads the language from the transcript's script."""
        return self.stt_model.startswith("whisper")

    @property
    def stt_response_format(self) -> str:
        return "verbose_json" if self.stt_reports_language else "json"

    @property
    def llm_extra(self) -> dict[str, Any]:
        """Extra chat-completion arguments this model needs. Empty for
        Groq and OpenAI's non-reasoning models."""
        if self.name == "openai" and self.llm_model.startswith(_REASONING_PREFIXES):
            return {"reasoning_effort": "none"}
        return {}


def choose_provider_name(environ: Mapping[str, str]) -> str | None:
    """Explicit `SAATHI_AI_PROVIDER` wins; otherwise OpenAI if its key
    is set (the demo choice), else Groq if its key is set, else None."""
    explicit = environ.get("SAATHI_AI_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in PROVIDERS:
            raise ProviderUnavailable(
                f"SAATHI_AI_PROVIDER={explicit!r}; expected one of {', '.join(PROVIDERS)}"
            )
        return explicit
    if environ.get("OPENAI_API_KEY"):
        return "openai"
    if environ.get("GROQ_API_KEY"):
        return "groq"
    return None


def _key_name(name: str) -> str:
    return "OPENAI_API_KEY" if name == "openai" else "GROQ_API_KEY"


def missing_key(environ: Mapping[str, str] | None = None) -> str | None:
    """The env var that would have to be set for a provider to exist,
    or None when one is usable. For startup messages; never a value."""
    environ = os.environ if environ is None else environ
    try:
        name = choose_provider_name(environ)
    except ProviderUnavailable as exc:
        return str(exc)
    if name is None:
        return "OPENAI_API_KEY or GROQ_API_KEY"
    key = _key_name(name)
    return None if environ.get(key) else key


def provider_from_env(
    environ: Mapping[str, str] | None = None, *, client: Any = None
) -> AIProvider:
    """Build the provider the environment asks for. `client` is for
    tests: it replaces the real SDK client and skips the key check."""
    environ = os.environ if environ is None else environ
    name = choose_provider_name(environ)
    if name is None:
        raise ProviderUnavailable("set OPENAI_API_KEY or GROQ_API_KEY")
    if name == "openai":
        llm = environ.get("SAATHI_LLM_MODEL") or OPENAI_LLM_MODEL
        stt = environ.get("SAATHI_STT_MODEL") or OPENAI_STT_MODEL
    else:
        llm = environ.get("SAATHI_LLM_MODEL") or GROQ_LLM_MODEL
        stt = environ.get("SAATHI_STT_MODEL") or GROQ_STT_MODEL
    if client is None:
        key = environ.get(_key_name(name))
        if not key:
            raise ProviderUnavailable(f"{_key_name(name)} is not set in the environment")
        if name == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=key)
        else:
            from groq import Groq

            client = Groq(api_key=key)
    return AIProvider(name=name, client=client, llm_model=llm, stt_model=stt)
