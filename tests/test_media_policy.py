"""saathi/screen/static/js/media-policy.js — the browser's two pure
decisions (duck level by core state; body layout classes by panel view),
run in the real runtime they ship to.

There is no `node` on the device or the dev box, and adding one for a
test would be a new dependency for two functions. A headless Chromium is
already what the face runs in, so this test drives the module there: a
throwaway page imports it, runs the cases, and writes the results into
the DOM for `--dump-dom` to return. Skipped, not failed, where no
Chromium binary exists (CI runners have one; that's what makes this a
real gate there).
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

POLICY_PATH = (
    Path(__file__).parent.parent / "saathi" / "screen" / "static" / "js" / "media-policy.js"
)

_CASES_JS = """
import * as policy from "%(policy)s";
const out = {};
out.duck_listening = policy.effectiveVolume(70, "listening");
out.duck_thinking = policy.effectiveVolume(70, "thinking");
out.duck_speaking = policy.effectiveVolume(70, "speaking");
out.duck_handoff = policy.effectiveVolume(70, "handoff");
out.idle = policy.effectiveVolume(70, "idle");
out.attentive = policy.effectiveVolume(70, "attentive");
out.sleeping = policy.effectiveVolume(70, "sleeping");
out.clamped_high = policy.effectiveVolume(250, "idle");
out.clamped_low = policy.effectiveVolume(-5, "idle");
out.garbage = policy.effectiveVolume("loud", "idle");
out.layout_none = policy.layoutClasses("none", true);
out.layout_results = policy.layoutClasses("results", false);
out.layout_results_fs = policy.layoutClasses("results", true);
out.layout_player = policy.layoutClasses("player", false);
out.layout_player_fs = policy.layoutClasses("player", true);
out.all = policy.ALL_LAYOUT_CLASSES;
out.duck_factor = policy.DUCK_FACTOR;
document.body.setAttribute("data-out", JSON.stringify(out));
"""


def _chromium() -> str | None:
    for candidate in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


@pytest.fixture(scope="module")
def policy_results() -> dict:
    binary = _chromium()
    if binary is None:
        pytest.skip("no chromium binary on this machine; media-policy.js not exercised")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "policy.html"
        page.write_text(
            "<!DOCTYPE html><html><body></body>"
            '<script type="module">'
            + _CASES_JS % {"policy": POLICY_PATH.resolve().as_uri()}
            + "</script></html>"
        )
        proc = subprocess.run(
            [
                binary,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--allow-file-access-from-files",
                "--virtual-time-budget=2000",
                "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env={"HOME": tmp, "PATH": "/usr/bin:/bin"},
        )
    match = re.search(r'data-out="([^"]*)"', proc.stdout)
    assert match, f"policy page produced no output: {proc.stderr[-500:]}"
    return json.loads(match.group(1).replace("&quot;", '"'))


def test_a_press_ducks_the_video_to_a_fifth_and_holds_it_through_the_reply(policy_results):
    assert policy_results["duck_factor"] == 0.2
    assert policy_results["duck_listening"] == 14
    assert policy_results["duck_thinking"] == 14
    assert policy_results["duck_speaking"] == 14
    assert policy_results["duck_handoff"] == 14


def test_idle_attentive_and_sleeping_play_at_the_asked_for_level(policy_results):
    assert policy_results["idle"] == 70
    assert policy_results["attentive"] == 70
    assert policy_results["sleeping"] == 70


def test_volume_is_clamped_and_garbage_is_silent_not_a_crash(policy_results):
    assert policy_results["clamped_high"] == 100
    assert policy_results["clamped_low"] == 0
    assert policy_results["garbage"] == 0


def test_the_face_is_in_every_layout(policy_results):
    # No layout hides #face-container: "none" is face-only, the others
    # are face-beside or face-in-the-corner. Fullscreen only applies to
    # the player; results always sit beside the face.
    assert policy_results["layout_none"] == []
    assert policy_results["layout_results"] == ["media--panel"]
    assert policy_results["layout_results_fs"] == ["media--panel"]
    assert policy_results["layout_player"] == ["media--panel"]
    assert policy_results["layout_player_fs"] == ["media--fullscreen"]
    assert set(policy_results["all"]) == {"media--panel", "media--fullscreen"}
