"""Hanging up: press and hold the spacebar for two seconds.

Exists to define exactly what calling needs from the screen's hold
seam, and nothing more. The screen server (`screen/server.py`, SCREEN's
territory) owns the spacebar and the Holding card: it measures hold
duration server-side (the browser sends one `press` and one `release`
per hold) and broadcasts progress 0 -> 1 as a Holding card. Calling only
registers "when a hold completes, hang up". `FakeHoldSeam` is what tests
and the live dial script use until SCREEN's real seam lands.

Contested: a double-tap lost. Double-tap timing is hard for older hands
and easy to trigger by accident; a two-second hold is deliberate and
forgiving, and a single tap during a call does nothing at all — so
nothing she does by reflex can end a call with her daughter.

Contested: the hold length is fixed here (`HANGUP_HOLD_SECONDS`), not a
preference. Two seconds is long enough to be intentional and short
enough not to feel stuck; a knob for it would be a knob nobody asked for.
"""

from __future__ import annotations

from typing import Callable, Protocol

HANGUP_HOLD_SECONDS = 2.0
HANGUP_LABEL = "Hang up"


class HoldSeam(Protocol):
    """What calling needs from `screen/server.py`'s hold handling.

    `set_handler`: from now on, a press held for at least `seconds`
    fires `on_hold_complete()` (once per hold, on the server's event
    loop thread), a shorter tap does nothing, and the Holding card shows
    progress with `label`. `clear`: back to ordinary spacebar behaviour.
    """

    def set_handler(
        self, on_hold_complete: Callable[[], None], *, seconds: float, label: str
    ) -> None: ...

    def clear(self) -> None: ...


class FakeHoldSeam:
    """Records what was registered and lets a test (or the live dial
    script) simulate a hold of a given length."""

    def __init__(self) -> None:
        self.handler: Callable[[], None] | None = None
        self.seconds: float | None = None
        self.label: str | None = None
        self.cleared = 0

    def set_handler(
        self, on_hold_complete: Callable[[], None], *, seconds: float, label: str
    ) -> None:
        self.handler = on_hold_complete
        self.seconds = seconds
        self.label = label

    def clear(self) -> None:
        self.handler = None
        self.seconds = None
        self.label = None
        self.cleared += 1

    def simulate_hold(self, held_for: float) -> bool:
        """Returns whether the hold was long enough to fire the handler."""
        if self.handler is None or self.seconds is None or held_for < self.seconds:
            return False
        self.handler()
        return True
