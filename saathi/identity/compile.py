"""State -> context block, run between turns only.

Not built at checkpoint 1 — nothing calls a voice engine yet to hand
context to. Exists to do exactly one translation: rows with confidence and
provenance (as stored) into plain sentences a model acts on (as sent) —
"She likes being greeted by name. Don't ask how she slept." SPEC.md is
explicit that this never runs *during* a turn; the hot path is the latency
budget the whole system protects, and compiling context is not free.
"""
