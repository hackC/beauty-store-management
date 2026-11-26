# -*- coding: utf-8 -*-
"""V1.6.0-dev.3 network resilience regression checks."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_release_version():
    main = _read("main.py")
    assert 'APP_VERSION = "1.6.0-rc.18"' in main


def test_both_frontends_install_shared_resilient_transport():
    for rel in ("static/index.html", "static/index_V2_5_5.html"):
        html = _read(rel)
        assert '/static/store_network.js?v=1.6.0-rc.7' in html
        assert "window.fetch =" not in html


def test_upgrade_payload_is_synchronized():
    for rel in ("main.py", "static/index.html", "static/index_V2_5_5.html"):
        assert (ROOT / rel).read_bytes() == (ROOT / "upgrade_payload" / rel).read_bytes()
