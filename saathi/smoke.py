"""`python -m saathi.smoke` — exercises real hardware and reports what is
plugged into this machine.

Checkpoint 1 only has one real hardware interface (`audio/devices.py`), so
this checks microphones and speakers. It gates every deploy (SPEC.md,
"Peripherals"): exit non-zero when something critical — no microphone, no
speaker — is down, so a missing device fails a deploy instead of surfacing
as "she can't hear it" during a home visit. Camera and screen checks join
this file once those modules exist; this is not the place to invent them
early.
"""

from __future__ import annotations

import sys
from typing import Sequence

from saathi.audio.devices import Device, DeviceManager, PulseAudioBackend


def _report(kind: str, devices: list[Device]) -> bool:
    """Print what was found for one direction. Returns whether it's ok."""
    if not devices:
        print(f"No {kind} found. Plug one in and try again.")
        return False
    print(f"{kind.capitalize()}s found ({len(devices)}):")
    for device in devices:
        print(f"  {device.description} [{device.bus or 'unknown bus'}]")
    return True


def main(argv: Sequence[str] | None = None, manager: DeviceManager | None = None) -> int:
    # `manager` is injectable so tests exercise the reporting/exit-code
    # logic against a FakeBackend, without a real PulseAudio server.
    manager = manager or DeviceManager(PulseAudioBackend())
    inputs_ok = _report("microphone", manager.enumerate("input"))
    outputs_ok = _report("speaker", manager.enumerate("output"))
    return 0 if inputs_ok and outputs_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
