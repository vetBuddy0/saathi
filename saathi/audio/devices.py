"""Enumerate, choose, and hotplug-watch audio hardware.

No device name, card number or `plughw:` string is ever written into code or
config here — that is the mistake CLAUDE.md calls out as the most expensive
bug in the previous build, because a card number written to a file goes
stale the moment something is unplugged. Everything in this module is
discovered at runtime from PulseAudio and re-discovered on every hotplug
event; the only thing persisted to disk is *which device id worked before*
(`_WorkingDeviceMemory`), and even that is looked up by whatever id
PulseAudio currently assigns, never assumed to be stable across reboots.

Contested decision: this machine runs Ubuntu 20.04 with PulseAudio, not
PipeWire — SPEC.md's architecture diagram names PipeWire because that's
what AEC (checkpoint 2) wants, but device enumeration has no reason to
require it. `pactl` here is also the PulseAudio 13.x on this box, which
predates `-f json` (that landed in PulseAudio 16); the parser below reads
the classic indented text format on purpose rather than assuming JSON
support exists.

Real hardware vs. PulseAudio's own virtual devices (monitors, the
echo-cancel filter pair) is told apart via the `device.class` property
PulseAudio itself attaches (`sound` for hardware, `monitor`/`filter` for
virtual) rather than by name matching, which would break the moment a
vendor names their loopback device differently.

Hotplug is watched via `pyudev` against the `sound` subsystem — event
driven, not polled, and independent of whether PulseAudio or PipeWire is
running underneath.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

Direction = Literal["input", "output"]

_BLOCK_RE = re.compile(r"^(Source|Sink) #(\d+)\n((?:.*\n?)*?)(?=\n\S|\Z)", re.MULTILINE)
_FIELD_RE = re.compile(r"^\t(Name|Description): (.*)$", re.MULTILINE)
_PROP_RE = re.compile(r'^\t{1,2}([\w.]+) = "(.*)"$', re.MULTILINE)

# Virtual devices PulseAudio itself creates (monitors, the AEC filter pair).
# Real capture/playback hardware is always tagged device.class = "sound".
_VIRTUAL_CLASSES = {"monitor", "filter"}


@dataclass(frozen=True)
class Device:
    """One audio endpoint, as PulseAudio currently sees it. `id` is
    PulseAudio's own object name — opaque and re-derived every call, never
    hardcoded."""

    id: str
    description: str
    direction: Direction
    bus: str | None  # "usb", "pci", ... or None if PulseAudio didn't say

    @property
    def is_usb(self) -> bool:
        return self.bus == "usb"


def _parse_pactl_list(output: str, direction: Direction) -> list[Device]:
    devices = []
    for match in _BLOCK_RE.finditer(output + "\n\n"):
        block = match.group(3)
        fields = dict(_FIELD_RE.findall(block))
        props = dict(_PROP_RE.findall(block))
        if props.get("device.class") in _VIRTUAL_CLASSES:
            continue
        name = fields.get("Name")
        if not name:
            continue
        devices.append(
            Device(
                id=name,
                description=fields.get("Description", name),
                direction=direction,
                bus=props.get("device.bus"),
            )
        )
    return devices


class DeviceBackend(Protocol):
    """What `DeviceManager` needs from an audio system. `PulseAudioBackend`
    talks to the real machine; `FakeBackend` lets the suite run headless."""

    def list_devices(self) -> list[Device]: ...

    def watch(self, on_change: Callable[[], None]) -> Callable[[], None]:
        """Call `on_change()` whenever hardware appears or disappears.
        Returns a `stop()` callable."""
        ...


class PulseAudioBackend:
    """Talks to whatever PulseAudio-compatible server is running via
    `pactl`, and watches udev directly for hotplug so it does not depend on
    PulseAudio's own (patchier) device-added/removed notifications."""

    def __init__(self, timeout: float = 3.0) -> None:
        self._timeout = timeout

    def _pactl(self, *args: str) -> str:
        # Degrade rather than die (SPEC.md, "Peripherals"): no PulseAudio
        # server running, or `pactl` missing entirely, is reported as "no
        # devices found" by the caller, not a crash.
        try:
            result = subprocess.run(
                ["pactl", *args], capture_output=True, text=True, timeout=self._timeout
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return ""
        return result.stdout

    def list_devices(self) -> list[Device]:
        sources = _parse_pactl_list(self._pactl("list", "sources"), "input")
        sinks = _parse_pactl_list(self._pactl("list", "sinks"), "output")
        return sources + sinks

    def watch(self, on_change: Callable[[], None]) -> Callable[[], None]:
        import pyudev

        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="sound")
        observer = pyudev.MonitorObserver(monitor, callback=lambda _device: on_change())
        observer.start()
        return observer.stop


class FakeBackend:
    """A backend the suite can drive by hand, so audio tests run without
    real hardware or a running PulseAudio server (SPEC.md: "audio, camera
    and screen behind interfaces with fakes")."""

    def __init__(self, devices: list[Device] | None = None) -> None:
        self._devices = list(devices or [])
        self._listeners: list[Callable[[], None]] = []

    def list_devices(self) -> list[Device]:
        return list(self._devices)

    def watch(self, on_change: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(on_change)

        def stop() -> None:
            if on_change in self._listeners:
                self._listeners.remove(on_change)

        return stop

    def set_devices(self, devices: list[Device]) -> None:
        """Simulate a hotplug event: swap the device list and notify."""
        self._devices = list(devices)
        for listener in list(self._listeners):
            listener()


class _WorkingDeviceMemory:
    """Persists which device *ids* have previously produced non-silent
    audio, so a repeat plug-in is preferred over a device that has never
    proven itself. Looked up by id on every call — nothing here assumes an
    id from a past boot still refers to the same physical device."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._ids: set[str] = set()
        if path and path.exists():
            try:
                self._ids = set(json.loads(path.read_text()))
            except (json.JSONDecodeError, OSError):
                self._ids = set()

    def __contains__(self, device_id: str) -> bool:
        return device_id in self._ids

    def mark_working(self, device_id: str) -> None:
        self._ids.add(device_id)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(sorted(self._ids)))


class DeviceManager:
    """Enumerates, chooses, and watches for hotplug. Preference order for
    `choose()`: a device that has worked before, then USB over onboard,
    then whatever PulseAudio listed first — "prefer USB capture over
    onboard; prefer a device that has produced non-silent audio before"
    (SPEC.md)."""

    def __init__(self, backend: DeviceBackend, memory_path: Path | None = None) -> None:
        self._backend = backend
        self._memory = _WorkingDeviceMemory(memory_path)
        self._stop_watch: Callable[[], None] | None = None

    def enumerate(self, direction: Direction | None = None) -> list[Device]:
        devices = self._backend.list_devices()
        if direction is not None:
            devices = [d for d in devices if d.direction == direction]
        return devices

    def choose(self, direction: Direction) -> Device | None:
        candidates = self.enumerate(direction)
        if not candidates:
            return None

        def rank(device: Device) -> tuple[bool, bool]:
            return (device.id not in self._memory, not device.is_usb)

        return min(candidates, key=rank)

    def mark_working(self, device: Device) -> None:
        self._memory.mark_working(device.id)

    def start_hotplug(self, on_change: Callable[[list[Device]], None]) -> Callable[[], None]:
        """Re-enumerate and call `on_change(devices)` whenever hardware
        changes. Returns a `stop()` callable; safe to call `start_hotplug`
        only once per manager."""

        def _handle_change() -> None:
            on_change(self.enumerate())

        self._stop_watch = self._backend.watch(_handle_change)
        return self._stop_watch
