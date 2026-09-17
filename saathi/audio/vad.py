"""Silero voice activity detection — not built at checkpoint 1.

Exists to answer "has she stopped talking" for the cascade voice engine and
for closing the open-mic window after Saathi speaks unprompted. Realtime
engines may do this themselves; `cascade.py` cannot.
"""
