"""MeloTTS (MIT) -- listed in the brief, not implemented, because it
cannot be installed into this project as pinned. Verified directly
against PyPI's own metadata, not assumed:

  - `melotts` ships only a source sdist on PyPI -- no wheels at all, for
    any platform.
  - It pins `torch<2.0`. Checked torch 1.13.1, 1.12.1, and 1.11.0 on
    PyPI: all three cap out at `cp310`/`cp311` wheels. None publish a
    `cp312` wheel on any platform, so `torch<2.0` cannot be satisfied at
    all on this project's pinned Python 3.12 -- independent of x86_64
    vs. aarch64.
  - It additionally pulls in `gradio` and several old, tightly pinned
    NLP toolkits (`mecab-python3`, `pykakasi`, `gruut`, `g2pkk`, ...),
    none of which this project needs for anything else.

This class exists so MeloTTS shows up in the settings panel and the cost
comparison the same way every other backend does, rather than being
silently missing -- `available()` always returns `False` with this exact
reason. Nothing else on this class is meaningful: `synthesize_stream()`
raises if ever called, because there is no working implementation to
call, and that is a bug in whatever called it, not a normal unavailable
state to degrade gracefully into.
"""

from __future__ import annotations

from typing import Iterator

from saathi.voice.tts import TTSBackend

_UNAVAILABLE_REASON = (
    "MeloTTS cannot be installed on this project: melotts ships no wheels "
    "(sdist only) and pins torch<2.0, which has no Python 3.12 wheel on any "
    "platform. Not an aarch64-specific problem."
)


class MeloTTSBackend(TTSBackend):
    id = "melotts"
    display_name = "MeloTTS (not installable)"
    license = "MIT"
    local = True

    def available(self) -> tuple[bool, str]:
        return False, _UNAVAILABLE_REASON

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        raise RuntimeError(_UNAVAILABLE_REASON)

    def cost_per_million_chars_usd(self) -> float:
        return 0.0
