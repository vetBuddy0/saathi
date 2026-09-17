"""openWakeWord integration — not built at checkpoint 1.

Exists so the across-the-room fallback (SPEC.md, "Triggers") has a home: the
spacebar is primary in v1, but a wearable button or a name spoken from
across the room needs local detection that never leaves the device — "the
privacy architecture, not an optimisation." Fires the `notice` event that
`core.py`'s `ATTENTIVE` state already has a transition for.
"""
