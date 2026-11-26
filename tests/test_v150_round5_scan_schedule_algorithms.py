from pathlib import Path


def test_scanner_uses_adaptive_multirow_ean_decoder_and_native_fallback():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text("utf-8")
    assert "function eanOtsuThreshold(gray)" in html
    assert "function eanRunsFromGray(gray, mode=0)" in html
    assert "function decodeEAN13Left(runs,at)" in html
    assert "validEANCenterGuard" in html
    assert "frameCount % 3 === 0" in html
    assert "const thoroughPass = frameCount % 12 === 0" in html
    assert "decodeEANCanvas(canvas, false, {quick: !thoroughPass})" in html
    assert "whiteBalanceMode" in html
    assert "function toggleBarcodeTorch()" in html


def test_schedule_auto_assignment_balances_staff_load_and_solves_full_group():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text("utf-8")
    assert "AUTO_ASSIGN_BALANCE_WINDOW = 10" in html
    assert "function getStaffDayLoad(staffId,date,appointments,extraMinutes=0)" in html
    assert "la.projected-lb.projected" in html
    assert "ScheduleGroup.solve(guests.map(g=>optionsFor(g,false)))" in html


def test_development_payload_matches_primary_scanner_and_schedule_assets():
    root = Path(__file__).resolve().parents[1]
    for relative in ("main.py", "static/index.html", "static/index_V2_5_5.html"):
        assert (root / relative).read_bytes() == (root / "upgrade_payload" / relative).read_bytes()


def test_schedule_auto_assign_runtime_scenario_when_node_is_available():
    import shutil
    import subprocess
    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; runtime scheduling scenario is supplied as a standalone test")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests" / "test_v150_round5_schedule_auto_assign.cjs")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
