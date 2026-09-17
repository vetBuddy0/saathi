"""A `Transport` that talks to an engine running on-device, no network hop.

Not built at checkpoint 1. Exists for the case a future engine is small
enough to run on the kiosk machine itself — same `Transport` interface, so
`voice/session.py` cannot tell the difference.
"""
