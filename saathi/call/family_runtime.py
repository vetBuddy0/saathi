"""Everything the free family app needs at run time, built in one place.

Exists so `cli.py` gains one call (`FamilyRuntime.build(...)`) rather
than a dozen, and so `screen/server.py` sees one object with the three
seams it uses -- `set_broadcast` (device messages out), `on_device_message`
(device messages in) and `install_local_routes` (the Ctrl+P pairing
endpoints) -- the same shape as the cards/media/panel seams beside it.

Bring-up runs on a daemon thread at boot, like the Twilio relay
(DECISIONS 2026-09-26): with no `SAATHI_PUBLIC_URL`, a quick tunnel can
take a minute or more to resolve, and the face must not wait for it.
Until it is up, a paired person is answered "still starting up"; the
rest of the device never depends on it.

On with `SAATHI_FAMILY_APP=on`; off by default. Contested: the owner
chose the family app as the way she calls, so "on by default" was the
obvious option -- it lost because turning it on exposes a server to the
internet (through the tunnel) and generates the device's push identity.
Opening a port to the internet should be a line someone wrote in
`~/.saathi/env`, not a side effect of updating the code.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from saathi.call.family import FamilyRegistry
from saathi.call.family_server import (
    DEFAULT_PORT,
    FamilyServer,
    build_family_app,
    install_local_routes,
    public_url_from_env,
)
from saathi.call.ice import IceConfig
from saathi.call.push import PushSender, Vapid
from saathi.call.relay import CloudflaredQuickTunnel, Relay, find_cloudflared
from saathi.call.routing import FamilyRoute
from saathi.call.webrtc import FamilyCalls

logger = logging.getLogger(__name__)

TUNNEL_STARTUP_SECONDS = 150.0
FAMILY_DB = "family.sqlite3"


def enabled(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("SAATHI_FAMILY_APP", "off").strip().lower() in ("on", "1", "true", "yes")


@dataclass
class FamilyRuntime:
    registry: FamilyRegistry
    calls: FamilyCalls
    server: FamilyServer
    route: FamilyRoute
    stable_url: str | None
    tunnel: Relay | None
    ready: threading.Event = field(default_factory=threading.Event)
    reason: str = "The family app is still starting up."
    notes: list[str] = field(default_factory=list)
    # The URL phones reach right now (None until up); set by build().
    public_url: Callable[[], str | None] = field(default=lambda: None)

    @classmethod
    def build(
        cls,
        data_dir: Path,
        *,
        hold: Any,
        cards: Any,
        panel: Callable[[Any], None] | None,
        emotions: Any = None,
        other_active: Callable[[], bool] = lambda: False,
        env: dict[str, str] | None = None,
    ) -> "FamilyRuntime":
        """Raises ValueError on bad configuration (a malformed
        `SAATHI_PUBLIC_URL` or `SAATHI_ICE_SERVERS`); the caller reports
        it and carries on without the family app."""
        env = dict(os.environ) if env is None else env
        stable = public_url_from_env(env)
        ice = IceConfig.from_env(env)
        port = int(env.get("SAATHI_FAMILY_PORT", DEFAULT_PORT))
        registry = FamilyRegistry(Path(data_dir) / FAMILY_DB)
        vapid = Vapid.load_or_create(Path(data_dir))
        subject = env.get("SAATHI_VAPID_SUBJECT", "").strip() or (
            stable or "mailto:saathi@example.invalid"
        )
        push = PushSender(vapid, subject)
        tunnel: Relay | None = None
        if stable is None and find_cloudflared() is not None:
            tunnel = CloudflaredQuickTunnel(port, startup_timeout=TUNNEL_STARTUP_SECONDS)

        holder: dict[str, FamilyRuntime] = {}

        def public_url() -> str | None:
            runtime = holder.get("runtime")
            if runtime is None or not runtime.ready.is_set():
                return None
            if stable is not None:
                return stable
            try:
                return tunnel.public_url if tunnel is not None else None
            except Exception:
                return None

        calls = FamilyCalls(
            registry,
            push=push,
            public_url=public_url,
            hold=hold,
            cards=cards,
            panel=panel,
            emotions=emotions,
            ice_servers=ice.servers,
            other_active=other_active,
        )
        server = FamilyServer(lambda: build_family_app(registry, calls, push), port=port)

        def not_ready() -> str | None:
            runtime = holder["runtime"]
            return None if runtime.ready.is_set() else runtime.reason

        runtime = cls(
            registry=registry,
            calls=calls,
            server=server,
            route=FamilyRoute(registry, calls, not_ready),
            stable_url=stable,
            tunnel=tunnel,
        )
        holder["runtime"] = runtime
        runtime.public_url = public_url
        if not ice.has_turn:
            runtime.notes.append(
                "Family app: STUN only (no TURN configured) -- calls from some mobile "
                "networks may not connect; see docs/DEMO.md."
            )
        return runtime

    # -- lifecycle -------------------------------------------------------------

    def start_in_background(self) -> None:
        threading.Thread(target=self._bring_up, name="saathi-family-up", daemon=True).start()

    def _bring_up(self) -> None:
        try:
            self.server.start()
            if self.stable_url is None:
                if self.tunnel is None:
                    raise RuntimeError(
                        "no SAATHI_PUBLIC_URL and cloudflared is not installed, so phones "
                        "can't reach the family app"
                    )
                self.tunnel.start()
        except Exception as exc:
            self.reason = f"The family app isn't available on this device: {exc}"
            logger.warning("family app off: %s", exc)
            return
        self.reason = ""
        self.ready.set()
        if self.stable_url is not None:
            logger.info("family app at %s/family/", self.stable_url)
        else:
            logger.info(
                "family app on a quick tunnel (%s/family/) -- the address changes on restart; "
                "set SAATHI_PUBLIC_URL for a permanent one",
                self.public_url(),
            )

    def shutdown(self) -> None:
        self.calls.shutdown()
        if self.tunnel is not None:
            self.tunnel.stop()
        self.server.stop()

    # -- the screen server's seams -----------------------------------------------

    def set_broadcast(self, send) -> None:
        self.calls.set_broadcast(send)

    def on_device_message(self, payload: dict) -> None:
        self.calls.on_device_message(payload)

    def install_local_routes(self, app) -> None:
        install_local_routes(
            app,
            self.registry,
            self.public_url,
            stable=self.stable_url is not None,
            unavailable_reason=lambda: self.reason or None,
        )
