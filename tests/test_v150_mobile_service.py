# -*- coding: utf-8 -*-
"""V1.5.0 移动端与 Windows 后台恢复静态回归检查。"""
from pathlib import Path


def test_mobile_inventory_and_schedule_responsive_rules():
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "static" / "index.html").read_text(encoding="utf-8")
    schedule = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")

    assert "V1.5.0 mobile-first refinement" in inventory
    assert ".op-row" in inventory
    assert "mobile-card-table product-table" in inventory
    assert "mobile-card-table purchase-table" in inventory
    assert 'data-label="当前库存"' in inventory
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in inventory
    assert "safe-area-inset-bottom" in inventory
    assert "viewport-fit=cover" in schedule
    assert "V1.5.0 mobile-first refinement for a 5–10 person shop" in schedule
    assert ".actions .nav-group" in schedule
    axis_css = (root / "static" / "schedule_display.css").read_text(encoding="utf-8")
    assert "height: calc(100svh - 152px)" in axis_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in schedule
    assert "white-space: nowrap" in schedule


def test_windows_background_task_restarts_failed_headless_server():
    root = Path(__file__).resolve().parents[1]
    main = (root / "main.py").read_text(encoding="utf-8")
    install = (root / "install_autostart.ps1").read_text(encoding="utf-8-sig")
    start = (root / "start_server.bat").read_text(encoding="utf-8-sig")
    stop = (root / "stop_server.bat").read_text(encoding="utf-8-sig")
    status = (root / "check_server.ps1").read_text(encoding="utf-8-sig")

    assert 'raise SystemExit(1)' in main
    assert '-AtStartup' in install
    assert "-UserId 'SYSTEM'" in install
    assert '-RestartCount 999' in install
    assert '-RestartInterval (New-TimeSpan -Minutes 1)' in install
    assert 'schtasks /Run' in start
    assert "--stop-local" in stop
    assert "taskkill" not in stop.lower()
    assert "/ENABLE" not in start
    assert 'Test-NetConnection' in status
    upgrade = (root / 'apply_upgrade.ps1').read_text(encoding='utf-8-sig')
    assert '1\\.4\\.0' in upgrade
    assert 'V1.5.0 源码已覆盖' in upgrade
