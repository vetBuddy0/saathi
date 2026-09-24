"""Serves the face to the Chromium kiosk and holds its WebSocket.

Deliberately thin: `server.py` relays `core.py`'s state to the browser and
spacebar input back, and nothing else. It is not the `Transport` used to
talk to a voice engine (SPEC.md's architecture diagram draws these as two
separate wires) and it never decides state itself — that stays `core.py`'s
job alone.

Two things sit beside the face on that screen and are relayed the same
way (2026-09-25): the media panel (`tools/media.py` decides, the server
carries) and cards (`cards.py` — the four primitives for asking her
something by tap or voice, a module other streams import rather than
drawing their own). Both are content, never status; see `cards.py`'s
docstring for why that distinction matters.
"""
