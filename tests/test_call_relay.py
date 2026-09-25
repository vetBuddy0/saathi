"""saathi/call/relay.py — URL parsing, and the quick tunnel against a
stand-in binary (a real child process printing a URL, not a mock)."""

import stat
import time

import pytest

from saathi.call.relay import CloudflaredQuickTunnel, FakeRelay, RelayError, parse_quick_tunnel_url


def test_parses_the_url_out_of_either_log_format():
    boxed = "2026-09-25T00:00:00Z INF |  https://tidy-words-here.trycloudflare.com          |"
    plain = "INF Your quick Tunnel has been created! Visit it at: https://a-b-c.trycloudflare.com"
    assert parse_quick_tunnel_url(boxed) == "https://tidy-words-here.trycloudflare.com"
    assert parse_quick_tunnel_url(plain) == "https://a-b-c.trycloudflare.com"
    assert parse_quick_tunnel_url("INF Requesting new quick Tunnel on trycloudflare.com...") is None


def _script(tmp_path, body: str) -> str:
    path = tmp_path / "fake-cloudflared"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_quick_tunnel_starts_reads_the_url_and_stops(tmp_path):
    script = _script(
        tmp_path,
        "echo \"$@\" > \"$(dirname \"$0\")/args\"\n"
        'echo "INF |  https://fake-tunnel-name.trycloudflare.com  |" >&2\n'
        "sleep 30\n",
    )
    tunnel = CloudflaredQuickTunnel(8768, binary=script, startup_timeout=5.0)
    tunnel.start()
    assert tunnel.public_url == "https://fake-tunnel-name.trycloudflare.com"
    args = (tmp_path / "args").read_text()
    assert "--url http://127.0.0.1:8768" in args
    started_stop = time.monotonic()
    tunnel.stop()
    assert time.monotonic() - started_stop < 5.0
    with pytest.raises(RelayError):
        tunnel.public_url


def test_quick_tunnel_times_out_when_no_url_is_printed(tmp_path):
    script = _script(tmp_path, 'echo "INF starting..." >&2\nsleep 30\n')
    tunnel = CloudflaredQuickTunnel(8768, binary=script, startup_timeout=0.5)
    with pytest.raises(RelayError):
        tunnel.start()


def test_quick_tunnel_reports_a_missing_binary_plainly(tmp_path):
    tunnel = CloudflaredQuickTunnel(8768, binary=str(tmp_path / "nope"), startup_timeout=1.0)
    with pytest.raises((RelayError, FileNotFoundError)):
        tunnel.start()


def test_fake_relay_counts_lifecycle():
    relay = FakeRelay("https://x.example")
    relay.start()
    relay.stop()
    assert (relay.started, relay.stopped, relay.public_url) == (1, 1, "https://x.example")
