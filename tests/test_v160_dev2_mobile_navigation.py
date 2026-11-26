# -*- coding: utf-8 -*-
"""V1.6.0-dev.2 regression checks for the schedule page navigation."""
from pathlib import Path
import re


def _schedule_html():
    root = Path(__file__).resolve().parents[1]
    return (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")


def test_schedule_page_name_is_exactly_daily_schedule():
    html = _schedule_html()
    assert re.search(r"<title>每日排班</title>", html)
    assert '<h1 id="scheduleSystemName">每日排班</h1>' in html
    assert "identity.system_name} · 每日排班" not in html
    assert "storeBrand.textContent = identity.system_name" in html


def test_mobile_drawer_has_explicit_accessible_toggle_and_overlay():
    html = _schedule_html()
    assert 'id="scheduleMenuToggle"' in html
    assert 'aria-controls="scheduleSidebar"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="scheduleMobileOverlay" aria-hidden="true"' in html
    assert 'onclick="toggleScheduleMobileMenu()"' not in html
    assert 'onclick="toggleScheduleMobileMenu(false)"' not in html
    assert "scheduleMenuToggleButton.addEventListener('click'" in html
    assert "scheduleMenuOverlay.addEventListener('click'" in html


def test_drawer_state_updates_sidebar_overlay_and_accessibility():
    html = _schedule_html()
    start = html.index("function toggleScheduleMobileMenu(force)")
    end = html.index("async function logoutScheduleUser()", start)
    block = html[start:end]
    assert "sidebar.classList.toggle('mobile-open', shouldOpen)" in block
    assert "overlay.classList.toggle('show', shouldOpen)" in block
    assert "toggle.setAttribute('aria-expanded', String(shouldOpen))" in block
    assert "document.body.classList.toggle('schedule-drawer-open', shouldOpen)" in block


def test_mobile_schedule_menus_are_full_width_in_flow_accordions():
    html = _schedule_html()
    css_start = html.index("/* V1.6.0-dev.2: mobile drawer and in-viewport schedule menus */")
    css_end = html.index("</style>", css_start)
    css = html[css_start:css_end]
    assert "@media (max-width:640px)" in css
    assert ".schedule-module-actions .schedule-nav" in css
    assert "flex-direction:column!important" in css
    assert "position:static!important" in css
    assert "display:none!important" in css
    assert "display:grid!important" in css
    assert "width:100%!important" in css
    assert "grid-template-columns:repeat(2,minmax(0,1fr))!important" in css
    assert "overflow-wrap:anywhere!important" in css


def test_mobile_drawer_has_explicit_stack_and_open_state():
    html = _schedule_html()
    css_start = html.index("/* V1.6.0-dev.2: mobile drawer and in-viewport schedule menus */")
    css_end = html.index("</style>", css_start)
    css = html[css_start:css_end]
    assert "z-index:130!important" in css
    assert "transform:translate3d(-105%,0,0)!important" in css
    assert "transform:translate3d(0,0,0)!important" in css
    assert "z-index:120!important" in css


def test_mobile_drawer_closes_after_navigation_escape_and_wide_resize():
    html = _schedule_html()
    assert "if (link && window.matchMedia('(max-width:640px)').matches) toggleScheduleMobileMenu(false)" in html
    assert "if (event.key === 'Escape') toggleScheduleMobileMenu(false)" in html
    assert "if (window.innerWidth > 640) toggleScheduleMobileMenu(false)" in html


def test_schedule_page_keeps_unified_store_shell_and_single_mobile_navigation():
    html = _schedule_html()
    assert 'class="store-shell app-frame"' in html
    assert 'class="store-sidebar"' in html
    assert 'class="store-mobile-nav app-bottom-nav"' in html
    assert 'class="schedule-mobile-bar"' not in html
    assert 'data-store-page="schedule"' in html


def test_schedule_navigation_menu_mutex_logic_is_retained():
    html = _schedule_html()
    assert "function closeUnrelatedScheduleMenus(targetMenu)" in html
    assert "closeUnrelatedScheduleMenus(menu)" in html
