"""Saathi: a companion device for an elderly person living alone.

This package exists so identity — who Saathi is to one specific person —
lives in a file (`identity/store.py`) instead of inside a model, a prompt, or
a vendor's API. Everything else (voice engine, transport, hardware) is
replaceable underneath that file. See SPEC.md for the contract and
CLAUDE.md for the rules this codebase follows while it is being built.
"""
