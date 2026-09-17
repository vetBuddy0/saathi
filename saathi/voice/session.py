"""`VoiceSession` — one of the five interfaces, not built at checkpoint 1.

Exists to define `start`, `send_audio`, `say`, `on_audio`, `on_intent`,
`interrupt` once there are two implementations (`realtime`, `cascade`) that
must behave identically from `core.py`'s point of view — "recognisably the
same character" regardless of engine (SPEC.md, checkpoint 2). `say()` is the
one entry point that does not require a prior spacebar press: SPEC.md's
open-mic-after-Saathi-speaks behaviour only exists because AEC makes it
safe, so this module has a hard dependency on `audio/aec.py` landing first.
"""
