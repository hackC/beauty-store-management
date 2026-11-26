from pathlib import Path
import shutil
import subprocess

import pytest


def test_rc7_logout_waits_for_server_and_quick_booking_reassigns():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the schedule workflow simulation")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [node, str(root / "tests" / "rc7_schedule_case.cjs"),
         str(root / "static" / "index_V2_5_5.html")],
        timeout=10, check=True,
    )
