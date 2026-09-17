"""Serves the face to the Chromium kiosk and holds its WebSocket.

Deliberately thin: `server.py` relays `core.py`'s state to the browser and
spacebar input back, and nothing else. It is not the `Transport` used to
talk to a voice engine (SPEC.md's architecture diagram draws these as two
separate wires) and it never decides state itself — that stays `core.py`'s
job alone.
"""
