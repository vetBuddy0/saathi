"""Which STUN/TURN servers a family call's two browsers are told to use.

Exists because whether a WebRTC call connects is decided by NAT, not by
code: two home wifi networks usually connect with STUN alone (free,
public, no account), but a phone on a carrier network behind symmetric
NAT/CGNAT needs a TURN relay, and a TURN relay costs someone something.
So the list is configuration, never code, and the default is the free
part only:

- `SAATHI_ICE_SERVERS` -- a JSON list of `RTCIceServer` objects, used
  as-is (e.g. a self-hosted coturn with static credentials).
- `SAATHI_TURN_CLOUDFLARE_KEY_ID` + `SAATHI_TURN_CLOUDFLARE_API_TOKEN` --
  Cloudflare's TURN service (free tier at the time of writing): short-
  lived credentials are minted per call from those two values and added
  to the list. Nothing is signed up for by this code; docs/DEMO.md says
  how the owner gets the two values.
- Neither: public STUN only, and a call that can't traverse the NAT
  ends after `webrtc.CONNECT_TIMEOUT_SECONDS` with a log line naming
  TURN as the likely fix.

Contested: minting Cloudflare credentials on the device rather than
putting a long-lived TURN username/password in the page. The API token
stays on the device; what reaches a phone expires (24 h).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_STUN = [
    {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.l.google.com:19302"]},
]
CLOUDFLARE_TTL_SECONDS = 24 * 3600
_CLOUDFLARE_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate"

# (url, body, headers, timeout) -> parsed JSON. Injectable for tests.
JsonPoster = Callable[[str, bytes, dict[str, str], float], Any]


def _post_json(url: str, body: bytes, headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _valid_server(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    urls = entry.get("urls")
    if isinstance(urls, str):
        urls = [urls]
    return (
        isinstance(urls, list)
        and bool(urls)
        and all(
            isinstance(u, str) and u.split(":", 1)[0] in ("stun", "turn", "turns") for u in urls
        )
    )


def parse_ice_servers(text: str | None) -> list[dict]:
    """`SAATHI_ICE_SERVERS`. Raises ValueError on anything that isn't a
    list of `{urls: ...}` objects -- a typo here would otherwise show up
    as calls that silently never connect."""
    if not text or not text.strip():
        return [dict(server) for server in DEFAULT_STUN]
    data = json.loads(text)
    if not isinstance(data, list) or not all(_valid_server(entry) for entry in data):
        raise ValueError("SAATHI_ICE_SERVERS must be a JSON list of {\"urls\": ...} objects")
    return data


class IceConfig:
    def __init__(
        self,
        static: list[dict],
        cloudflare: tuple[str, str] | None = None,
        post: JsonPoster = _post_json,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._static = static
        self._cloudflare = cloudflare
        self._post = post
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: tuple[float, list[dict]] | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "IceConfig":
        env = os.environ if env is None else env
        static = parse_ice_servers(env.get("SAATHI_ICE_SERVERS"))
        key_id = env.get("SAATHI_TURN_CLOUDFLARE_KEY_ID", "").strip()
        token = env.get("SAATHI_TURN_CLOUDFLARE_API_TOKEN", "").strip()
        return cls(static, (key_id, token) if key_id and token else None)

    @property
    def has_turn(self) -> bool:
        if self._cloudflare is not None:
            return True
        return any(
            any(str(u).startswith("turn") for u in _urls(server)) for server in self._static
        )

    def servers(self) -> list[dict]:
        """The list for one call. A Cloudflare failure falls back to the
        static list (logged), never to no call at all."""
        if self._cloudflare is None:
            return list(self._static)
        with self._lock:
            now = self._clock()
            if self._cached is not None and now < self._cached[0]:
                return self._static + self._cached[1]
            key_id, token = self._cloudflare
            try:
                data = self._post(
                    _CLOUDFLARE_URL.format(key_id=key_id),
                    json.dumps({"ttl": CLOUDFLARE_TTL_SECONDS}).encode(),
                    {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    5.0,
                )
                turn = _cloudflare_servers(data)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                logger.warning("TURN credentials unavailable (%s); STUN only", type(exc).__name__)
                return list(self._static)
            # Refresh at half-life so a phone never gets nearly-dead ones.
            self._cached = (now + CLOUDFLARE_TTL_SECONDS / 2, turn)
            return self._static + turn


def _urls(server: dict) -> list:
    urls = server.get("urls", [])
    return [urls] if isinstance(urls, str) else list(urls)


def _cloudflare_servers(data: Any) -> list[dict]:
    servers = data.get("iceServers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        servers = [servers]
    if not isinstance(servers, list) or not all(_valid_server(s) for s in servers):
        raise ValueError("unexpected TURN credentials response")
    return servers
