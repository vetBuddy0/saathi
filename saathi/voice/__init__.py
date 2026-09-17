"""Talking to a voice engine: the `VoiceSession` interface, its transports,
and its two engine implementations. None of this is built at checkpoint 1 —
checkpoint 1 has no AI. See SPEC.md, "The five interfaces": the voice engine
never executes anything; it emits intent, `core.py` validates, a tool
executes.
"""
