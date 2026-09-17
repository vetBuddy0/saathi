"""Audio I/O: device discovery, capture, playback, AEC, wake word, VAD.

Split from `voice/` because these modules talk to hardware and PulseAudio
directly, while `voice/` talks to a voice engine over a `Transport`. Keeping
that boundary means the audio layer can be faked in tests without faking a
network client, and vice versa.
"""
