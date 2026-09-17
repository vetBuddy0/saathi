"""`Transport` — one of the five interfaces, not built at checkpoint 1.

Carries audio and intent between `voice/session.py` and a remote or local
engine. Kept separate from `screen/server.py`'s WebSocket, which only ever
carries face state to the browser and spacebar events back — two different
wires in SPEC.md's architecture diagram, not one interface wearing two hats.
"""
