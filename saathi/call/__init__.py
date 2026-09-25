"""Phone calls — Twilio Media Streams over a public relay, into the same
echo-cancelled audio path the cascade uses.

Exists because SPEC.md's v2 promise ("their tool stubs exist in v1 so v2
swaps an implementation rather than inventing plumbing") is now being
cashed: `tools/calling.py` replaces `call_contact`'s stub handler and
nothing else. Everything here sits *behind* that handler — the voice
engine emits the intent, `tools/registry.py` validates the `"calls"`
permission, and this package executes.

Shape: a call is a WebSocket Twilio opens *inwards* to us, carrying 20 ms
frames of 8 kHz μ-law in both directions — the same "audio in, audio out
over a socket" shape as the cascade, which is why it reuses the cascade's
echo-cancelled source and sink rather than growing its own audio path
(she must not be heard twice by the person on the line).

Contested: SIP and LiveKit both lost. SIP needs a registrar, NAT
traversal and a media stack this project would have to own; LiveKit is a
second always-on service and a new vendor for the one thing Twilio
already does over a plain WebSocket. Media Streams is the smallest thing
that rings a real phone.

Contested: the device is behind home wifi and Twilio connects inwards, so
a public relay is unavoidable. `relay.py` isolates that as a replaceable
piece — a login-free cloudflared quick tunnel today, a real always-on
service in production (see `docs/completed/calling.md` for exactly what
that service must be).
"""
