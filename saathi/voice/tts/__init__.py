"""One source of truth for which TTS backends exist, whether each is
usable right now, and what synthesizing through it costs.

This module exists because of a barge-in bug, not a feature request:
Piper's `synthesize_wav()` is one blocking call with no way to abort it
mid-sentence, and on a Raspberry Pi — several times slower than the
laptop this was built on — that call can take long enough that
*synthesis itself*, not anything in `screen/server.py`'s locking, is why
a press doesn't interrupt her. `TTSBackend.synthesize_stream()` yields
one chunk of audio per sentence instead of the whole reply at once, and
`voice/engine/cascade.py`'s `_speak()` checks for an interrupt between
chunks. For a persona capped at two sentences, that bounds the
un-interruptible window to roughly one sentence — close to the best a
batch-synthesis backend (Piper, Kokoro) can do without true streaming of
its own. Google's backend uses the real streaming synthesis API and gets
this for free; it goes through the same chunked interface anyway, so
`_speak()` has one code path instead of two.

Every backend here is instantiated eagerly (cheap — none of them load a
model or open a network connection at construction) and checked lazily:
`available()` is called right before use, not at import time, because
whether Google's credentials exist can change while the process is
running (someone dropped the service account key in after boot) and
nothing should have to restart to notice.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Iterator


def split_into_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation followed by whitespace.
    Deliberately simple — this is a barge-in responsiveness boundary, not
    a linguistics project. A missed split just means one chunk covers two
    sentences, which only widens the interrupt window back toward what it
    was before chunking; it never breaks correctness."""
    text = text.strip()
    if not text:
        return []
    # CJK full stops (。！？) end a sentence with no space after them, so
    # they split on their own; without this a whole Chinese reply was one
    # chunk (one barge-in window, one voice) -- 2026-09-26.
    parts = re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text)
    return [p.strip() for p in parts if p and p.strip()]


class TTSBackend(ABC):
    id: str
    display_name: str
    license: str
    local: bool  # True: no network, $0/month. False: cloud, has a real cost.

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """`(True, "")` if this backend can be used right now, else
        `(False, reason)` — e.g. missing GCP credentials. Never raises:
        an unavailable backend is a normal, expected state to show in the
        settings panel greyed out with its reason, not an error. Same
        principle as `voice/language.py`: unavailable and visibly so
        beats silently broken."""

    @abstractmethod
    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        """Yield one WAV file (as bytes) per entry in `sentences`, in
        order. Each yield should be produced only when asked for (a
        generator, not a pre-computed list) — the caller relies on that
        to check for an interrupt before paying for the next sentence's
        synthesis, not just before playing what's already been made."""

    @abstractmethod
    def cost_per_million_chars_usd(self) -> float:
        """0.0 for local backends. For cloud ones, Google's own published
        price — see the concrete class's docstring for the exact figure
        and the date it was checked; never trust this number silently
        going stale."""
