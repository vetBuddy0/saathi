"""The APK's Python is enough: the engine starts, with the real session,
when every package the phone cannot have is unimportable.

Chaquopy has no wheels for pysilero-vad, soundfile, piper-tts,
onnxruntime, pyudev, pywebrtc-audio, grpcio, or the openai and groq SDKs
(pydantic-core), so android/app/build.gradle.kts installs only aiohttp,
numpy, requests, google-auth and cryptography, and the engine's import
guards cover the rest -- each proven in its own test (test_vad,
test_cascade, test_tts, test_provider, ...). This file proves the sum:
`saathi.android.start()` the way the app calls it, in a subprocess where
every one of those packages is absent at once, with an AI key so the
real `CascadeSession` is built (test_android_entry.py uses a fake one),
and `GET /` answers. An absent package is `sys.modules[name] = None`,
which is what the import system reports for a package that is not
installed -- `import` raises, and `importlib.util.find_spec` returns
None, which matters: yarl (under aiohttp) asks `find_spec("pydantic_core")`
on import, and a finder that raised there, as the first draft of this
probe did, took the server thread down with an error no phone would see.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

# What Chaquopy cannot provide (no wheel, or a native build the phone
# cannot do), as the importable names. `google.cloud` is the SDK the
# Google voices use where it exists; on the phone they go over REST.
ABSENT_ON_ANDROID = (
    "pysilero_vad",
    "soundfile",
    "piper",
    "onnxruntime",
    "pyudev",
    "pywebrtc_audio",
    "grpc",
    "openai",
    "groq",
    "pydantic_core",
    "pydantic",
    "google.cloud",
    "faster_whisper",
    "kokoro",
    "torch",
)


def test_the_engine_starts_with_only_the_packages_the_apk_carries(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    data_dir = tmp_path / "files"  # context.filesDir on the phone
    probe = textwrap.dedent(
        f"""
        import json, sys, threading, urllib.request
        for name in {ABSENT_ON_ANDROID!r}:
            sys.modules[name] = None
        from saathi import android
        from saathi.voice.engine import cascade

        built = []
        real = cascade.CascadeSession

        class Noted(real):
            def __init__(self, *args, **kwargs):
                built.append(real.__name__)
                super().__init__(*args, **kwargs)

        cascade.CascadeSession = Noted
        keys = {{"GROQ_API_KEY": "fake-groq", "YOUTUBE_API_KEY": "fake-youtube"}}
        started = android.start({{"data_dir": {str(data_dir)!r}, "port": 0, "keys": keys}})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
        with opener.open(started["url"] + "/", timeout=5) as response:
            status = response.status
        android.stop()
        print(json.dumps({{
            "status": status,
            "notes": started["notes"],
            "sessions": built,
            "running": android._running,
            "engine_threads": [t.name for t in threading.enumerate() if t.name == "saathi-engine"],
        }}))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    # Logging goes to stderr; the report is the one line on stdout.
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == 200
    assert report["sessions"] == ["CascadeSession"]  # the real one, built once
    assert any("SAATHI_AUDIO=remote" in note for note in report["notes"])
    assert report["running"] is None and report["engine_threads"] == []
    assert (data_dir / "identity.sqlite3").exists()
    # Nothing was fetched into the home directory (a voice model, say).
    assert list(home.iterdir()) == []
