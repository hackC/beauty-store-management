# -*- coding: utf-8 -*-
"""Regression tests for iPhone Safari scan button responsiveness."""
from pathlib import Path


def test_scan_modal_and_camera_request_start_before_any_await():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("async function startBarcodeScan(targetId,")
    end = html.index("function scanBarcodeVideoFrames", start)
    fn = html[start:end]
    assert 'modal?.classList.add("show")' in fn
    assert 'cameraRequest = navigator.mediaDevices.getUserMedia' in fn
    assert fn.index('modal?.classList.add("show")') < fn.index('cameraRequest = navigator.mediaDevices.getUserMedia')
    # getUserMedia is invoked before the first await so iOS Safari retains the click gesture.
    assert fn.index('cameraRequest = navigator.mediaDevices.getUserMedia') < fn.index("await cameraRequest")
    assert "await startZXingBarcodeScan()" not in fn


def test_safari_has_local_live_frame_and_photo_fallbacks():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert 'scanBarcodeVideoFrames(null)' in html
    assert 'const value = decodeEANCanvas(canvas, true, {quick:false})' in html
    assert 'function captureBarcodeFrame()' in html
    assert 'onclick="captureBarcodeFrame()"' in html
    assert 'id="barcodePhotoInput"' in html
    assert 'capture="environment"' in html


def test_scanner_cleanup_cancels_pending_scan_timer():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("function stopBarcodeScan(hideModal = true)")
    end = html.index("// 启动", start)
    fn = html[start:end]
    assert "window.clearTimeout(barcodeScanTimer)" in fn
    assert "barcodeScanTimer = null" in fn
