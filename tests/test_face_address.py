"""The face page moves itself from 127.0.0.1 to localhost.

Why it is pinned: YouTube's embed refuses most music videos (error 150)
to a page whose address is an IP, and plays the same video at localhost
(2026-10-28, DECISIONS.md). The face at 127.0.0.1 showed the player for
a split second and closed it. The real index.html's head is served over
real HTTP on 127.0.0.1; the body is swapped for a probe that reports the
address the page ended up on.
"""

import html
import http.server
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from tests.chromium_harness import chromium_binary

INDEX = Path(__file__).parent.parent / "saathi" / "screen" / "static" / "index.html"
PROBE = "<body><script>document.body.dataset.out = location.hostname;</script></body>"


def _final_hostname(user_agent: str | None = None) -> str:
    binary = chromium_binary()
    if binary is None:
        pytest.skip("no chromium binary on this machine")
    page = re.sub(r"<body>.*</body>", PROBE, INDEX.read_text(), flags=re.S).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    args = [binary, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--virtual-time-budget=3000", "--dump-dom", f"http://127.0.0.1:{port}/"]
    if user_agent:
        args.insert(1, f"--user-agent={user_agent}")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=90,
                                  env={"HOME": tmp, "PATH": "/usr/bin:/bin"})
    finally:
        server.shutdown()
    match = re.search(r'data-out="([^"]*)"', proc.stdout)
    assert match, f"page produced no output: {proc.stderr[-800:]}"
    return html.unescape(match.group(1))


def test_the_face_at_127_0_0_1_moves_to_localhost():
    assert _final_hostname() == "localhost"


def test_the_android_webview_stays_where_it_was_loaded():
    # The shell pins the face to its engine address and refuses any other
    # navigation; it plays YouTube in its own pane.
    ua = "Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"
    assert _final_hostname(ua) == "127.0.0.1"
