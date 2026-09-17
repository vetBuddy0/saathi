"""Acoustic echo cancellation — not built at checkpoint 1.

Exists so `voice/session.py`'s open-mic-after-Saathi-speaks behaviour
(SPEC.md, "State machine") has somewhere to plug in at checkpoint 2: that
behaviour is only safe once residual echo is bounded, and the bench test
(play a known tone, capture, assert residual below threshold) is what
proves it.

Try PipeWire's `module-echo-cancel` first, where available — it routes the
reference signal below the application, which is the hard part. Fall back
to `pywebrtc-audio` where it is not (this dev machine runs PulseAudio, which
also ships `module-echo-cancel`, so the fallback path matters here too, not
just on hardware PipeWire can't reach).
"""
