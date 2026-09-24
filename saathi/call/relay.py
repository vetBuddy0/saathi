"""The public relay: the one piece of this product that must be
reachable from the internet, because Twilio connects *inwards* and the
device sits behind home wifi.

Exists as a separate, replaceable piece on purpose. Today it is a
cloudflared *quick tunnel* — login-free, `cloudflared tunnel --url
http://127.0.0.1:8768` prints a random `https://<words>.trycloudflare.com`
URL on stderr and forwards it to the local media server. That is right
for a dev box and wrong for production: the hostname changes every
start, there is no uptime promise, and the tunnel process is a child of
ours. In production the relay is a real always-on service — the first
this product has — with a static hostname, TLS, Twilio request-signature
validation and a restart policy; `docs/completed/calling.md` says exactly
what it must be. Nothing outside this module knows which one is running:
`Relay` is `public_url`, `start()`, `stop()`.

Contested: an SSH reverse tunnel to a VPS lost (needs a VPS and a key on
the device); ngrok lost (a new vendor, and its free tier interposes a
browser warning page that breaks non-browser callers); a named
cloudflared tunnel lost *for now* only because it needs a Cloudflare
login on this box — it is the obvious production shape and the quick
tunnel is a stand-in for it.

cloudflared is Apache-2.0 (github.com/cloudflare/cloudflared). Installed
by hand to `~/.local/bin`, never vendored: release 2026.9.3,
`cloudflared-linux-amd64` sha256
77e26d8d900e0b8469f416239d14b5f296525fdf79fee6f511ef55609e3fbac2,
verified against GitHub's published asset digest on 2026-09-25.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Protocol

_QUICK_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class RelayError(RuntimeError):
    pass


class Relay(Protocol):
    @property
    def public_url(self) -> str: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


class FakeRelay:
    def __init__(self, public_url: str = "https://relay.example.test") -> None:
        self._public_url = public_url
        self.started = 0
        self.stopped = 0

    @property
    def public_url(self) -> str:
        return self._public_url

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


def parse_quick_tunnel_url(line: str) -> str | None:
    """The one line of cloudflared's stderr we care about. Its log format
    has changed across releases (boxed banner, then plain `INF` lines);
    matching the URL itself rather than the surrounding text survives
    both."""
    match = _QUICK_TUNNEL_URL_RE.search(line)
    return match.group(0) if match else None


def find_cloudflared() -> str | None:
    """PATH first, then the per-user install location `setup` puts it
    in. A binary location is not a device name; it is still looked up,
    never assumed."""
    found = shutil.which("cloudflared")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "cloudflared"
    return str(candidate) if candidate.is_file() else None


class CloudflaredQuickTunnel:
    """A login-free quick tunnel to `http://127.0.0.1:<local_port>`.
    `start()` blocks until the public URL has been printed or
    `startup_timeout` passes. `binary` is injectable so a test can point
    it at a stand-in script that prints a URL — a real subprocess, not
    a mocked one."""

    def __init__(
        self,
        local_port: int,
        binary: str | None = None,
        startup_timeout: float = 30.0,
    ) -> None:
        self._local_port = local_port
        self._binary = binary
        self._startup_timeout = startup_timeout
        self._proc: subprocess.Popen | None = None
        self._public_url: str | None = None
        self._url_ready = threading.Event()
        self._stderr_tail: list[str] = []

    @property
    def public_url(self) -> str:
        if self._public_url is None:
            raise RelayError("relay is not started")
        return self._public_url

    def start(self) -> None:
        binary = self._binary or find_cloudflared()
        if binary is None:
            raise RelayError("cloudflared is not installed (see saathi/call/relay.py)")
        self._proc = subprocess.Popen(
            [
                binary,
                "tunnel",
                "--no-autoupdate",
                "--url",
                f"http://127.0.0.1:{self._local_port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        if not self._url_ready.wait(self._startup_timeout):
            self.stop()
            raise RelayError(
                f"cloudflared printed no tunnel URL within {self._startup_timeout:.0f}s"
            )

    def _drain_stderr(self) -> None:
        # Keeps draining after the URL is found so the pipe never fills
        # and blocks cloudflared; keeps a short tail for diagnostics.
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr_tail = (self._stderr_tail + [line.rstrip()])[-20:]
            if self._public_url is None:
                url = parse_quick_tunnel_url(line)
                if url:
                    self._public_url = url
                    self._url_ready.set()

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self._public_url = None
        self._url_ready.clear()
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
