"""Mic frames, post-AEC — not built at checkpoint 1.

Exists as the seam between `audio/devices.py` (which device) and
`voice/session.py` (what the engine hears): this module turns a chosen
`Device` into a stream of clean PCM frames. Nothing upstream of it should
need to know it is PulseAudio underneath.
"""
