"""Runs a snippet of ES-module JavaScript in a headless Chromium and
returns what it wrote into `<body data-out=...>`.

Shared by the media-panel and media-policy tests. There is no `node` on
the device or the dev box, and a JS runtime as a dev dependency for a
few browser-only modules would be a build step in waiting; the face
already runs in Chromium, so the modules are tested in the runtime they
ship to. Tests skip (not fail) where no Chromium binary exists.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

STATIC_JS = Path(__file__).parent.parent / "saathi" / "screen" / "static" / "js"


def chromium_binary() -> str | None:
    for candidate in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def run_module_script(script: str, *, body_html: str = "") -> dict:
    """`script` is the body of a `<script type="module">`; it must end
    by setting `document.body.dataset.out` to a JSON string. Module
    imports use `module_url("media-policy.js")`-style absolute file
    URLs."""
    binary = chromium_binary()
    if binary is None:
        pytest.skip("no chromium binary on this machine; browser modules not exercised")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(
            "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
            + body_html
            + '<script type="module">'
            + script
            + "</script></body></html>"
        )
        proc = subprocess.run(
            [
                binary,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--allow-file-access-from-files",
                "--virtual-time-budget=3000",
                "--window-size=1920,1080",  # the kiosk's screen; sizes are asserted in px
                "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            env={"HOME": tmp, "PATH": "/usr/bin:/bin"},
        )
    match = re.search(r'data-out="([^"]*)"', proc.stdout)
    assert match, f"page produced no output: {proc.stderr[-800:]}"
    return json.loads(html.unescape(match.group(1)))


def module_url(name: str) -> str:
    return (STATIC_JS / name).resolve().as_uri()


def stylesheet_link() -> str:
    """A <link> to the real style.css, for tests that assert computed
    sizes rather than logic."""
    href = (STATIC_JS.parent / "css" / "style.css").resolve().as_uri()
    return f'<link rel="stylesheet" href="{href}">'
