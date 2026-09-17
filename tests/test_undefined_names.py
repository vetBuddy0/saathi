"""The undefined-name check CLAUDE.md calls out by name: "a rename that
missed one call site already shipped a crash on this project." Runs as
both a CI step and a test so it fails the same way locally.
"""

import subprocess
import sys


def test_no_undefined_names_in_the_package():
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select=F821", "saathi"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
