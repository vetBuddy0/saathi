"""call/family_server.py -- the internet-facing family app, over
aiohttp's TestClient: pairing, push subscription, the signaling socket's
authentication, and the pairing QR that only the device itself can
mint. Plus call/ice.py's configuration."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from saathi.call import ice
from saathi.call.family import FamilyRegistry
from saathi.call.family_server import (
    build_family_app,
    install_local_routes,
    public_url_from_env,
)
from saathi.call.hangup import FakeHoldSeam
from saathi.call.webrtc import FamilyCalls

SUB_JSON = {
    "endpoint": "https://push.test/endpoint",
    "keys": {
        "p256dh": (
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
        ),
        "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    },
}


class FakePush:
    public_key = "BPUBLIC"

    def send(self, *_a, **_k):
        pass


def _app(tmp_path):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    calls = FamilyCalls(registry, push=FakePush(), public_url=lambda: "https://s.test",
                        hold=FakeHoldSeam())
    calls._ensure_ticker = lambda: None
    app = build_family_app(registry, calls, FakePush(), run_blocking=lambda fn, *a: fn(*a))
    return app, registry, calls


async def _pair(client, registry):
    token, _ = registry.issue_pairing_token()
    response = await client.post(
        "/family/api/pair",
        json={"token": token, "name": "Priya", "relation": "daughter", "calls_her": "Mum"},
    )
    return response, await response.json()


async def test_pairing_returns_a_key_once_and_the_token_is_spent(tmp_path):
    app, registry, _ = _app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        token, _ = registry.issue_pairing_token()
        body = {"token": token, "name": "Priya", "relation": "daughter", "calls_her": "Mum"}
        response = await client.post("/family/api/pair", json=body)
        data = await response.json()
        assert response.status == 200 and data["ok"]
        assert data["vapid_public_key"] == "BPUBLIC" and data["calls_her"] == "Mum"
        assert registry.authenticate(data["member_id"], data["key"]).name == "Priya"
        again = await client.post("/family/api/pair", json=body)
        assert again.status == 400 and "already been used" in (await again.json())["error"]


async def test_subscribe_needs_the_member_key(tmp_path):
    app, registry, _ = _app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        _, paired = await _pair(client, registry)
        bad = await client.post("/family/api/subscribe", json={
            "member_id": paired["member_id"], "key": "wrong", "subscription": SUB_JSON})
        assert bad.status == 403
        good = await client.post("/family/api/subscribe", json={
            "member_id": paired["member_id"], "key": paired["key"], "subscription": SUB_JSON})
        assert good.status == 200
        assert registry.get(paired["member_id"]).subscription.endpoint == SUB_JSON["endpoint"]


async def test_the_socket_refuses_a_stranger_and_carries_a_members_call(tmp_path):
    app, registry, calls = _app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        _, paired = await _pair(client, registry)
        stranger = await client.ws_connect("/family/ws")
        await stranger.send_str(json.dumps({"type": "hello", "member": "x", "key": "y"}))
        assert json.loads((await stranger.receive()).data)["type"] == "unauthorized"
        await stranger.close()

        ws = await client.ws_connect("/family/ws")
        await ws.send_str(json.dumps(
            {"type": "hello", "member": paired["member_id"], "key": paired["key"]}))
        welcome = json.loads((await ws.receive()).data)
        assert welcome == {"type": "welcome", "name": "Priya", "calls_her": "Mum",
                           "can_call": True}
        await ws.send_str(json.dumps({"type": "call"}))
        waiting = json.loads((await ws.receive(timeout=2)).data)
        assert waiting["type"] == "waiting" and calls.active
        await ws.close()
        for _ in range(100):  # the socket closing ends the ring
            if not calls.active:
                break
            await asyncio.sleep(0.01)
        assert not calls.active


async def test_pages_carry_security_headers_and_the_worker_its_scope(tmp_path):
    app, _, _ = _app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        index = await client.get("/family/")
        assert index.status == 200 and "frame-ancestors 'none'" in index.headers[
            "Content-Security-Policy"]
        worker = await client.get("/family/sw.js")
        assert worker.headers["Service-Worker-Allowed"] == "/family/"
        manifest = await client.get("/family/manifest.webmanifest")
        assert (await manifest.json())["scope"] == "/family/"
        # The pairing QR is never on this (tunnelled) app.
        assert (await client.post("/family-local/pairing")).status in (404, 405)


async def test_the_pairing_qr_is_minted_only_for_the_device_itself(tmp_path):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    app = web.Application()
    install_local_routes(app, registry, lambda: "https://s.test", stable=False)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/family-local/pairing")
        data = await response.json()
        assert data["ok"] and data["url"].startswith("https://s.test/family/#pair=")
        assert data["qr_svg"].startswith("<svg") and "restarts" in data["note"]
        token = data["url"].split("#pair=")[1]
        member, _ = registry.redeem(token, "Priya")
        # Something the tunnel forwarded (Cloudflare adds Cf-* headers) is refused.
        forwarded = await client.post("/family-local/pairing", headers={"Cf-Ray": "1"})
        assert forwarded.status == 403
        unpaired = await client.post("/family-local/unpair", json={"member_id": member.id})
        assert (await unpaired.json())["ok"] and registry.members() == []


def test_public_url_must_be_https():
    assert public_url_from_env({}) is None
    assert public_url_from_env({"SAATHI_PUBLIC_URL": "https://saathi.example.org/"}) == (
        "https://saathi.example.org"
    )
    with pytest.raises(ValueError):
        public_url_from_env({"SAATHI_PUBLIC_URL": "http://saathi.example.org"})


# -- call/ice.py ---------------------------------------------------------------


def test_ice_defaults_to_free_public_stun_only():
    config = ice.IceConfig.from_env({})
    assert config.servers() == ice.DEFAULT_STUN and not config.has_turn


def test_ice_servers_from_config_are_validated():
    turn = [{"urls": "turn:turn.example.test:3478", "username": "u", "credential": "c"}]
    config = ice.IceConfig.from_env({"SAATHI_ICE_SERVERS": json.dumps(turn)})
    assert config.servers() == turn and config.has_turn
    with pytest.raises(ValueError):
        ice.parse_ice_servers('[{"urls": "http://not-ice"}]')


def test_cloudflare_turn_credentials_are_minted_cached_and_failures_fall_back():
    calls = []
    minted = {"iceServers": {"urls": ["turn:turn.cloudflare.com:3478"], "username": "u",
                             "credential": "c"}}

    def post(url, body, headers, timeout):
        calls.append((url, headers))
        return minted

    now = {"t": 0.0}
    config = ice.IceConfig(ice.DEFAULT_STUN, ("KEYID", "TOKEN"), post=post,
                           clock=lambda: now["t"])
    servers = config.servers()
    assert servers[-1]["urls"] == ["turn:turn.cloudflare.com:3478"]
    assert "KEYID" in calls[0][0] and calls[0][1]["Authorization"] == "Bearer TOKEN"
    config.servers()
    assert len(calls) == 1  # cached
    now["t"] = ice.CLOUDFLARE_TTL_SECONDS
    minted = {"unexpected": True}
    assert config.servers() == ice.DEFAULT_STUN  # STUN only, never no call


def test_twilio_turn_is_opt_in_and_needs_the_keys():
    keys = {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_API_KEY": "SK1", "TWILIO_API_SECRET": "s"}
    assert not ice.IceConfig.from_env(keys).has_turn  # keys alone start no bill
    assert ice.IceConfig.from_env({**keys, "SAATHI_TURN_TWILIO": "on"}).has_turn
    assert not ice.IceConfig.from_env({"SAATHI_TURN_TWILIO": "on"}).has_turn


def test_twilio_turn_credentials_keep_turn_entries_as_urls_and_fall_back():
    import base64

    calls = []
    minted = {"ttl": "14400", "ice_servers": [
        {"url": "stun:global.stun.twilio.com:3478", "urls": "stun:global.stun.twilio.com:3478"},
        {"url": "turn:global.turn.twilio.com:3478?transport=udp",
         "urls": "turn:global.turn.twilio.com:3478?transport=udp",
         "username": "u", "credential": "c"},
    ]}

    def post(url, body, headers, timeout):
        calls.append((url, body, headers))
        return minted

    now = {"t": 0.0}
    config = ice.IceConfig(ice.DEFAULT_STUN, post=post, clock=lambda: now["t"],
                           twilio=("AC1", "SK1", "secret"))
    servers = config.servers()
    assert servers[len(ice.DEFAULT_STUN):] == [{
        "urls": "turn:global.turn.twilio.com:3478?transport=udp",
        "username": "u", "credential": "c",
    }]
    url, body, headers = calls[0]
    assert "/Accounts/AC1/Tokens.json" in url and b"Ttl=" in body
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"SK1:secret").decode()
    config.servers()
    assert len(calls) == 1  # cached
    now["t"] = ice.TWILIO_TTL_SECONDS
    minted = {"code": 20003, "message": "Authenticate"}
    assert config.servers() == ice.DEFAULT_STUN  # STUN only, never no call


def test_the_pairing_qr_scales_to_its_box_instead_of_cropping():
    # A fixed width/height with no viewBox let the page's CSS crop the
    # code (found live). The SVG must carry a viewBox and no fixed size.
    from saathi.call.family_server import qr_svg

    svg = qr_svg("https://saathi.example.org/family/#pair=abc")
    head = svg[: svg.index(">")]
    assert "viewBox" in head
    assert "width=" not in head and "height=" not in head
