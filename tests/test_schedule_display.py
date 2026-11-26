from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_schedule_geometry_and_display_preferences():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for schedule display verification")
    files = [
        "static/schedule_display.js",
        "static/index_V2_5_5.html",
        "static/schedule_display.css",
    ]
    subprocess.run(
        [node, str(ROOT / "tests/schedule_display_case.cjs"), *(str(ROOT / name) for name in files)],
        check=True, timeout=15,
    )
    for name in files:
        assert (ROOT / name).read_bytes() == (ROOT / "upgrade_payload" / name).read_bytes()
