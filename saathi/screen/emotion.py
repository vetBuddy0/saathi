"""A short-lived feeling on the face: blush, happy, sad, surprised.

Exists so that "she said something sweet, the eyes blush" has exactly
one door, on the core side of the screen, and the voice engine never
holds a handle to the face. SPEC.md: "the face is driven by core.py,
never by the engine." Whatever decides an emotion (a reply's tone, an
initiative, a test) calls `EmotionController.show(name)`; the screen
server installs the broadcast on it the same way it does for cards and
media, and the browser only draws what arrives.

Validated here, not in the browser: an unknown name is refused and
logged, so a model that invents "smug" can't put an undefined look on
the screen, and the browser never has to guess. The allowed set is
small on purpose -- each one is a look `eyes-face.js` actually draws.

Every emotion ends by itself (`seconds`, clamped). The face falls back
to whatever core.py's state looks like; there is no "current emotion"
anyone has to remember to clear, and a reload simply shows the state.

Contested (2026-10-07): the product owner asked for the eyes to be
playful -- blushing, surprised, sad -- going beyond SPEC.md's caution
that eyebrows read as a children's illustration. The option that lost
was "warmth only" (the narrowing at the corners and nothing more): it
kept the face dignified but the owner found it flat in the demo. Still
no mouth and no eyebrows; see DECISIONS.md.

Not wired to the model's reply yet: deciding *when* to blush belongs to
the voice side, and the only interface-safe route is intent -> core ->
this. A tool for it would spend the one tool call per turn
(DECISIONS 2026-09-18), so it is not registered with the model here.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# "neutral" clears an emotion early. Every other name is a look the
# eyes face draws; the browser ignores none of these.
EMOTIONS = frozenset({"neutral", "happy", "blush", "sad", "surprised", "curious", "love"})

DEFAULT_SECONDS = 4.0
MIN_SECONDS = 0.5
MAX_SECONDS = 15.0

Broadcast = Callable[[dict], None]


class EmotionController:
    """Thread-safe: `show()` may be called from the executor thread a
    turn runs on; the broadcast the server installs hops to its loop."""

    def __init__(self, broadcast: Broadcast | None = None) -> None:
        self._broadcast = broadcast
        self._lock = threading.Lock()

    def set_broadcast(self, broadcast: Broadcast | None) -> None:
        with self._lock:
            self._broadcast = broadcast

    def show(self, emotion: str, seconds: float | None = None) -> bool:
        """Puts `emotion` on the face for `seconds` (default 4 s, clamped
        to 0.5-15 s). Returns False, and draws nothing, for a name not in
        `EMOTIONS` or a non-numeric duration."""
        if not isinstance(emotion, str) or emotion.strip().lower() not in EMOTIONS:
            logger.warning("emotion dropped: %r is not one the face draws", emotion)
            return False
        name = emotion.strip().lower()
        if seconds is None:
            seconds = DEFAULT_SECONDS
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            logger.warning("emotion dropped: duration %r is not a number", seconds)
            return False
        if seconds != seconds:  # NaN
            return False
        seconds = max(MIN_SECONDS, min(MAX_SECONDS, seconds))
        with self._lock:
            broadcast = self._broadcast
        if broadcast is not None:
            broadcast({"type": "emotion", "emotion": name, "seconds": seconds})
        return True

    def clear(self) -> bool:
        return self.show("neutral")
