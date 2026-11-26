# -*- coding: utf-8 -*-
"""V1.6.0 phase 1: unified schedule shell regression checks."""
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "id" and value:
                self.ids.append(value)


def test_schedule_uses_shared_store_shell_and_removes_standalone_header():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    for token in [
        'class="store-shell app-frame"',
        'class="store-sidebar"',
        'class="store-topbar app-topbar"',
        'class="store-content app-scroll"',
        'class="store-mobile-nav app-bottom-nav"',
        'id="scheduleUserBadge"',
        'id="scheduleLogoutBtn"',
        'id="scheduleNotificationsBtn"',
    ]:
        assert token in html
    assert 'class="schedule-mobile-bar"' not in html
    assert 'id="backToInventoryBtn"' not in html
    assert 'class="logo">✿' not in html


def test_shared_navigation_contains_main_store_sections_and_schedule_active():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    pages = set(re.findall(r'data-store-page="([^"]+)"', html))
    assert {
        "home", "operation", "products", "transactions", "purchase",
        "schedule", "archive", "settings", "data", "users",
        "controlcenter", "help"
    } <= pages
    assert html.count('data-store-page="schedule"') >= 2
    assert 'class="store-nav-link active" data-store-page="schedule"' in html
    assert 'class="active" data-store-page="schedule"' in html


def test_schedule_shell_keeps_schedule_actions_and_business_workspace():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    for token in [
        'id="scheduleCollabBtn"', 'id="shiftPlannerBtn"', 'id="staffManage"',
        'id="restManage"', 'id="serviceManage"', 'id="scheduleQuickAddTop"', 'id="hoursBtn"',
        'id="styleBtn"', 'id="dailyReportBtn"', 'id="dbBtn"',
        'id="exportBtn"', 'id="importBtn"', 'id="timeline"',
        'id="miniCalendar"', 'id="quickAdd"', 'id="editDayScheduleBtn"',
    ]:
        assert token in html
    assert 'class="schedule-module-actions"' in html


def test_schedule_shell_enforces_permission_aware_navigation_and_first_login_gate():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    assert "function storeSchedulePageAllowed(page)" in html
    assert "function setupScheduleShell(user)" in html
    assert "link.hidden = !storeSchedulePageAllowed(page)" in html
    assert "auth.user.must_set_password" in html
    assert "setupScheduleShell(auth.user)" in html
    assert "function logoutScheduleUser()" in html


def test_schedule_shell_has_mobile_drawer_identity_and_safe_area_layout():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    assert 'id="scheduleMobileOverlay"' in html
    assert "function toggleScheduleMobileMenu(force)" in html
    assert "env(safe-area-inset-bottom)" in html
    assert ".schedule-user-badge { display:inline-flex!important" in html
    assert "grid-template-columns:repeat(7,minmax(0,1fr))" in html


def test_main_app_supports_shared_shell_page_deep_links():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert 'new URLSearchParams(location.search).get("page")' in html
    assert "pageAllowed(requested) ? requested : navigationOrder.find(pageAllowed)" in html
    assert "go(page, {replace: true})" in html


def test_shell_and_schedule_actions_have_no_duplicate_static_ids():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    parser = IdCollector()
    parser.feed(html)
    duplicates = sorted({item for item in parser.ids if parser.ids.count(item) > 1})
    assert not duplicates, f"duplicate IDs: {duplicates}"


def test_schedule_static_file_is_synced_to_upgrade_payload():
    root = Path(__file__).resolve().parents[1]
    main = (root / "static" / "index_V2_5_5.html").read_bytes()
    payload = (root / "upgrade_payload" / "static" / "index_V2_5_5.html").read_bytes()
    assert main == payload


def test_frontend_scripts_pass_node_syntax_check_when_available():
    node = shutil.which("node")
    if not node:
        return
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
    script_path = root / "tests" / "_v160_schedule_shell_syntax_check.js"
    try:
        script_path.write_text("\n".join(scripts), encoding="utf-8")
        result = subprocess.run([node, "--check", str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        script_path.unlink(missing_ok=True)
