# -*- coding: utf-8 -*-
"""Regression tests for linear-only barcode scanning and redundant floating shortcut removal."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def test_scanner_ignores_qr_and_accepts_only_linear_formats():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("const LINEAR_BARCODE_FORMATS")
    end = html.index("function normalizeLinearBarcode", start)
    formats = html[start:end]
    assert '"qr_code"' not in formats
    for fmt in ('"ean_13"', '"ean_8"', '"upc_a"', '"upc_e"', '"code_128"', '"code_39"', '"itf"', '"codabar"'):
        assert fmt in formats
    scan_start = html.index("async function startBarcodeScan(targetId,")
    scan_end = html.index("function scanBarcodeVideoFrames", scan_start)
    scan_fn = html[scan_start:scan_end]
    assert 'const scanFormats = ["ean_13","ean_8","upc_a","upc_e","code_128","code_39","itf","codabar"]' in scan_fn


def test_retry_clears_old_barcode_and_selected_product_details():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("async function startBarcodeScan(targetId,")
    end = html.index("function scanBarcodeVideoFrames", start)
    scan_fn = html[start:end]
    assert "clearBarcodeTargetForRetry(targetId)" in scan_fn
    clear_start = html.index("function clearBarcodeTargetForRetry")
    clear_end = html.index("function loadZXingBrowser", clear_start)
    clear_fn = html[clear_start:clear_end]
    assert 'target.value = ""' in clear_fn
    assert 'delete row.dataset.productId' in clear_fn
    assert '".op-barcode"' in clear_fn
    assert '".op-qty"' in clear_fn


def test_scanner_requires_two_matching_valid_linear_reads_and_checks_gtin():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert "function validGtinCheckDigit(value)" in html
    assert "function registerBarcodeCandidate(rawValue, format = \"\")" in html
    assert "barcodeCandidateCount < 2" in html
    assert "code?.rawValue != null" in html
    assert 'LINEAR_BARCODE_FORMATS.has(String(code.format || "").toLowerCase())' in html
    assert "QR and invalid/partial reads are ignored silently" in html


def test_redundant_floating_home_shortcuts_removed_but_navigation_retained():
    root = Path(__file__).resolve().parents[1]
    main_html = (root / "static" / "index.html").read_text(encoding="utf-8")
    schedule_html = (root / "static" / "index_V2_5_5.html").read_text(encoding="utf-8")
    assert "floatingHome" not in main_html
    assert "home-float" not in main_html
    assert 'data-page="home"' in main_html
    assert "schedule-home-float" not in schedule_html
    assert 'data-store-page="schedule"' in schedule_html
    assert 'class="store-mobile-nav app-bottom-nav"' in schedule_html
    assert 'id="backToInventoryBtn"' not in schedule_html
    assert 'class="schedule-mobile-bar"' not in schedule_html


def test_upgrade_payload_static_assets_match_primary():
    root = Path(__file__).resolve().parents[1]
    for relative in ("static/index.html", "static/index_V2_5_5.html"):
        assert (root / relative).read_bytes() == (root / "upgrade_payload" / relative).read_bytes()


def test_frontend_scripts_pass_node_syntax_check_when_available():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if not node:
        return
    for relative in ("static/index.html", "static/index_V2_5_5.html"):
        html = (root / relative).read_text(encoding="utf-8")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write("\n".join(scripts))
            path = Path(handle.name)
        try:
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            path.unlink(missing_ok=True)


def test_linear_barcode_normalizer_runtime_when_node_is_available():
    node = shutil.which("node")
    if not node:
        return
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("const LINEAR_BARCODE_FORMATS")
    end = html.index("function registerBarcodeCandidate", start)
    helpers = html[start:end]
    script = helpers + r"""
const cases = [
  ["4006381333931", "ean_13", true],
  ["96385074", "ean_8", true],
  ["036000291452", "upc_a", true],
  ["04252614", "upc_e", true],
  ["4006381333932", "ean_13", false],
  ["1234567890123", "qr_code", false],
  ["ABC12345", "code_128", true]
];
for (const [value, format, expected] of cases) {
  const actual = normalizeLinearBarcode(value, format) !== null;
  if (actual !== expected) throw new Error(`${value}/${format}: ${actual} !== ${expected}`);
}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(script)
        path = Path(handle.name)
    try:
        result = subprocess.run([node, str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        path.unlink(missing_ok=True)
