"""The Android workflow runs the engine's suite the way ci.yml does, and
uploads the APK from where Gradle writes it.

Why this test exists: `.github/workflows/android.yml` is the page a
person opens to fetch the APK, and its promise is that a push which
breaks the Python is red there too -- the APK carries `saathi/` inside
it. That promise is a copy of ci.yml's job, and a copy drifts: a step
added to ci.yml (a new system package, a new check) that is not added
here leaves the Android page green on a push the CI page fails. This
pins the two together, and pins the artifact path to the one the Gradle
build produces (`android/app/build/outputs/apk/debug/app-debug.apk`), so
a renamed module or build type fails a test and not a download.

What lost: a YAML parser. PyYAML is not a dependency of the project and
one test is not a reason to add one; the workflow files are written so
that every `run:` is a single line and every value this test reads is a
plain scalar, and the test reads them with a regular expression. That
is a narrower check than parsing the files, and it is stated here so
nobody mistakes it for one: a step that drifts in its `uses:` or `with:`
is not caught, only its `run:` line. Nothing here runs a workflow; the
workflows are not runnable on Linux without GitHub's runners.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
ANDROID = WORKFLOWS / "android.yml"

# The APK AGP writes for `assembleDebug` of the module `app`, relative to
# the repository root (the workflow checks out the repository there).
APK_PATH = "android/app/build/outputs/apk/debug/app-debug.apk"

_RUN = re.compile(r"^\s*(?:-\s+)?run:\s*(.+?)\s*$", re.MULTILINE)
_PATH = re.compile(r"^\s*path:\s*(.+?)\s*$", re.MULTILINE)
_WORKING_DIRECTORY = re.compile(r"^\s*working-directory:\s*(.+?)\s*$", re.MULTILINE)


def _runs(text: str) -> list[str]:
    return _RUN.findall(text)


def test_the_android_workflow_runs_every_engine_step_ci_runs():
    ci_runs = _runs(CI.read_text())
    android_runs = _runs(ANDROID.read_text())
    assert ci_runs, "ci.yml has no run: steps; the regular expression no longer matches it"
    missing = [step for step in ci_runs if step not in android_runs]
    assert not missing, f"ci.yml steps android.yml does not run: {missing}"


def test_the_engine_steps_keep_their_order():
    ci_runs = _runs(CI.read_text())
    android_runs = _runs(ANDROID.read_text())
    positions = [android_runs.index(step) for step in ci_runs]
    assert positions == sorted(positions), "the engine steps run out of order in android.yml"


def test_the_apk_is_uploaded_from_where_gradle_writes_it():
    text = ANDROID.read_text()
    assert APK_PATH in _PATH.findall(text)
    assert "android" in _WORKING_DIRECTORY.findall(text), "Gradle must run inside android/"
    assert any("assembleDebug" in step for step in _runs(text))


def test_the_android_workflow_builds_on_the_android_branches():
    text = ANDROID.read_text()
    # The branches the task names, and a manual run.
    assert "- android-app" in text
    assert '- "android/**"' in text
    assert "workflow_dispatch:" in text
    assert "retention-days: 14" in text
    assert "name: saathi-debug-apk" in text or "'saathi-debug-apk'" in text
