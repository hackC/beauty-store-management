from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_inventory_and_schedule_pages_load_dev12_stylesheet():
    for rel in ("static/index.html", "static/index_V2_5_5.html"):
        page = read(rel)
        assert '/static/app_shell.css' in page
        assert '/static/dev12_responsive.css' in page
        assert 'data-app-shell="true"' in page
        assert 'data-dev12-ui="true"' in page
        assert '/static/dev12_ui.js' in page


def test_dev12_stylesheet_has_phone_tablet_and_short_viewport_rules():
    css = read("static/dev12_responsive.css")
    assert "@media (max-width: 640px)" in css
    assert "@media (min-width: 641px) and (max-width: 1100px)" in css
    assert "@media (max-height: 560px)" in css
    assert "env(safe-area-inset-bottom" in css
    assert "position: sticky" in css


def test_dev12_styles_cover_both_actual_page_shells():
    css = read("static/dev12_responsive.css")
    for selector in (".sidebar", ".store-sidebar", ".content", ".store-content",
                     ".table-wrap", ".timeline-wrap", ".mobile-nav", ".store-mobile-nav",
                     ".modal-box", ".modal-actions"):
        assert selector in css


def test_dev12_runtime_tracks_visual_viewport_and_dynamic_tables():
    js = read("static/dev12_ui.js")
    assert "window.visualViewport" in js
    assert "--dev12-viewport-height" in js
    assert "MutationObserver" in js
    assert "refreshLayout" in js
    assert "scrollWidth > region.clientWidth" in js


def test_upgrade_payload_static_files_match_runnable_static_files():
    for name in ("index.html", "index_V2_5_5.html", "dev12_responsive.css", "dev12_ui.js"):
        assert (ROOT / "static" / name).read_bytes() == (
            ROOT / "upgrade_payload" / "static" / name
        ).read_bytes()


def test_no_new_business_or_network_mutation_in_dev12_runtime():
    js = read("static/dev12_ui.js")
    assert "fetch(" not in js
    assert "XMLHttpRequest" not in js
    assert "localStorage" not in js
    assert "indexedDB" not in js


def test_page_entrypoints_and_existing_api_routes_are_unchanged():
    main = read("main.py")
    assert 'def home(request: Request, page: str' in main
    assert 'def schedule_home()' in main
    assert 'index_V2_5_5.html' in main
    assert 'index.html' in main
