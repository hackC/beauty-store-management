from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _prototype():
    return (ROOT / "design_preview" / "dev7_representative_pages.html").read_text(encoding="utf-8")


def test_dev7_design_preview_has_all_representative_pages():
    html = _prototype()
    for marker in ('id="page-home"', 'id="page-inventory"', 'id="page-schedule"'):
        assert marker in html


def test_dev7_design_preview_is_mobile_ready_and_has_safe_area():
    html = _prototype()
    assert 'name="viewport"' in html
    assert 'env(safe-area-inset-bottom' in html
    assert '@media(max-width:700px)' in html
    assert 'preview-mobile' in html


def test_dev7_design_preview_has_sticky_table_header_and_action_bar():
    html = _prototype()
    assert '.table th{position:sticky' in html
    assert '.bottom-action{position:sticky' in html


def test_dev7_design_preview_explicitly_disclaims_live_business_data():
    html = _prototype()
    assert '静态演示数据' in html
    assert '不会写入真实业务数据' in html


def test_dev7_design_docs_and_traceability_exist():
    for rel in (
        "docs/MODULE_BOUNDARIES_DEV7.md",
        "docs/PERMISSION_CATALOG_DRAFT_DEV7.md",
        "docs/FRONTEND_DESIGN_SYSTEM_DEV7.md",
        "V1_6_0_DEV7_REQUIREMENT_TRACEABILITY.json",
    ):
        assert (ROOT / rel).is_file()
