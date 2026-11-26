# -*- coding: utf-8 -*-
"""Regression coverage for exclusive, hierarchy-aware schedule navigation menus."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def test_schedule_nav_uses_exclusive_hierarchy_aware_menu_behavior():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    assert 'const scheduleNavRoot = document.querySelector(".schedule-nav")' in html
    assert 'function closeUnrelatedScheduleMenus(targetMenu)' in html
    assert 'openMenu.contains(targetMenu) || targetMenu.contains(openMenu)' in html
    assert 'if (!menu.open) closeUnrelatedScheduleMenus(menu)' in html
    assert 'if (event.key === "Escape") closeAllScheduleMenus()' in html
    assert 'if (!scheduleNavRoot.contains(event.target)) closeAllScheduleMenus()' in html


def test_schedule_nav_actions_close_their_own_dropdown():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    assert 'event.target.closest("button, a")' in html
    assert 'else closeAllScheduleMenus()' in html
    assert 'if (ownerMenu) ownerMenu.open = false' in html


def test_current_schedule_dropdowns_are_siblings_and_not_nested():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    nav_start = html.index('<nav class="actions schedule-nav"')
    nav_end = html.index("</nav>", nav_start)
    nav = html[nav_start:nav_end]
    assert nav.count('<details class="schedule-nav-menu">') == 2
    assert nav.count("<summary>") == 2
    assert nav.count("</details>") == 2
    assert nav.index("</details>") < nav.rindex("<details")


def test_navigation_logic_is_synced_to_upgrade_payload():
    root = Path(__file__).resolve().parents[1]
    main = (root / "static" / "index_V2_5_5.html").read_bytes()
    payload = (root / "upgrade_payload" / "static" / "index_V2_5_5.html").read_bytes()
    assert main == payload


def test_navigation_logic_node_runtime_for_siblings_and_hierarchy_when_available():
    node = shutil.which("node")
    if not node:
        return
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    start = html.index("function closeUnrelatedScheduleMenus")
    end = html.index("function closeAllScheduleMenus", start)
    helper = html[start:end]
    script = r'''
const siblingA = {open:true, contains(other){return false;}};
const siblingB = {open:true, contains(other){return false;}};
const nestedChild = {open:true, contains(other){return false;}};
const parent = {open:true, contains(other){return other === nestedChild;}};
const openMenus = [siblingA, siblingB, parent, nestedChild];
const scheduleNavRoot = {querySelectorAll(){return openMenus;}};
''' + helper + r'''
closeUnrelatedScheduleMenus(siblingB);
if (siblingA.open !== false || siblingB.open !== true || parent.open !== false || nestedChild.open !== false) {
  throw new Error("opening an unrelated sibling must close other branches");
}
const inner = {open:true, contains(other){return false;}};
const outer = {open:true, contains(other){return other === inner;}};
const unrelated = {open:true, contains(other){return false;}};
const nestedOpenMenus = [outer, inner, unrelated];
scheduleNavRoot.querySelectorAll = () => nestedOpenMenus;
closeUnrelatedScheduleMenus(inner);
if (outer.open !== true || inner.open !== true || unrelated.open !== false) {
  throw new Error("opening a child must preserve its parent and close unrelated branches");
}
'''
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
        f.write(script)
        js_path = Path(f.name)
    try:
        result = subprocess.run([node, str(js_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        js_path.unlink(missing_ok=True)


def test_frontend_scripts_pass_node_syntax_check_when_available():
    node = shutil.which("node")
    if not node:
        return
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
        f.write("\n".join(scripts))
        js_path = Path(f.name)
    try:
        result = subprocess.run([node, "--check", str(js_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        js_path.unlink(missing_ok=True)
