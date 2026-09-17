"""Decides when to stay quiet — a reason to speak, not the absence of a
reason not to (SPEC.md). Not built until checkpoint 3. Reads presence, quiet
hours, and whether something else is already happening (television, a
call) before `scheduler.py`'s trigger is allowed to reach speech.
"""
