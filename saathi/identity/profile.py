"""The family-visible, family-editable view over what Saathi has learned.

Not built until checkpoint 3, once `reflect.py` exists to produce rules
worth showing. Reads and writes `rules` through `identity/store.py` only —
never touches SQLite directly.
"""
