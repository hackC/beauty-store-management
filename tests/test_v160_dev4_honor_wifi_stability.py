# -*- coding: utf-8 -*-
"""V1.6.0-dev.4 regression checks for Android/Honor LAN connectivity."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_dev4_version_and_liveness_endpoint_is_public():
    main = read("main.py")
    assert 'APP_VERSION = "1.6.0-rc.18"' in main
    public = main[main.index("PUBLIC_PATHS = {"):main.index("}", main.index("PUBLIC_PATHS = {")) + 1]
    assert '"/api/health/live"' in public


def test_liveness_probe_is_independent_of_sqlite():
    main = read("main.py")
    start = main.index('@app.get("/api/health/live")')
    end = main.index('@app.get("/api/health")', start)
    block = main[start:end]
    assert "async def health_live(response: Response)" in block
    assert "db()" not in block
    assert '"Cache-Control"' in block
    assert '"no-store, no-cache, must-revalidate, max-age=0"' in block
    assert '"ok": True' in block


def test_legacy_health_endpoint_keeps_database_revision_contract():
    main = read("main.py")
    start = main.index('@app.get("/api/health")')
    end = main.index('@app.get("/api/summary")', start)
    block = main[start:end]
    assert "SELECT value FROM meta WHERE key='revision'" in block
    assert '"revision": int(revision_row[0]) if revision_row else 0' in block


def test_uvicorn_mobile_lan_keepalive_is_explicit():
    main = read("main.py")
    start = main.index("config = uvicorn.Config(")
    end = main.index("server_obj = uvicorn.Server", start)
    block = main[start:end]
    assert "timeout_keep_alive=8" in block
    assert "timeout_graceful_shutdown=REQUEST_DRAIN_SECONDS" in block
    assert "backlog=1024" in block


def test_upgrade_payload_contains_current_server_and_frontend_sources():
    for rel in ("main.py", "static/index.html", "static/index_V2_5_5.html"):
        assert (ROOT / rel).read_bytes() == (ROOT / "upgrade_payload" / rel).read_bytes()
