from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
from queue import SimpleQueue
import sqlite3
from types import SimpleNamespace
from zipfile import ZipFile

from store_inventory.platform.desktop_theme import ACCENT, NAV, configure_console
from test_v160_dev9_catalog_inventory_permissions import _close_project, _load_project


def test_console_uses_indigo_theme_settings():
    styles = {}
    theme = []
    adapter = SimpleNamespace(theme_names=lambda: ("clam", "vista"), theme_use=theme.append,
                              configure=lambda key, **attrs: styles.update({key: attrs}),
                              map=lambda key, **attrs: None)
    configure_console(None, lambda _: adapter)
    assert theme == ["clam"]
    assert styles["ConsoleNav.TFrame"]["background"] == NAV
    assert styles["ConsolePrimary.TButton"]["background"] == ACCENT
    assert styles["ConsoleReady.TLabel"]["foreground"] != styles["ConsoleIssue.TLabel"]["foreground"]


def test_desktop_reports_restorable_full_zip_and_manifest(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        image = main.PRODUCT_IMAGE_DIR / ("a" * 32 + ".jpg")
        image.write_bytes(b"fixture image")
        purchase = main.configured_dir("purchase_dir", main.PURCHASE_DIR)
        purchase.mkdir(parents=True, exist_ok=True)
        (purchase / "fixture.xlsx").write_bytes(b"fixture attachment")
        with closing(main.db()) as con, con:
            con.execute("""INSERT INTO products(id,name,image_filename,created_at,updated_at)
                           VALUES ('RC6-FULL','全量备份测试',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""", (image.name,))

        worker_state = SimpleNamespace(desktop_service=SimpleNamespace(create_backup=main.create_backup),
                                       _backup_results=SimpleQueue())
        main.Manager._run_backup(worker_state)
        ok, path = worker_state._backup_results.get_nowait()
        assert ok, path
        bundle = Path(path)
        assert bundle.name.endswith("_完整备份.zip")

        with ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert archive.testzip() is None
            for name, metadata in manifest["files"].items():
                payload = archive.read(name)
                assert len(payload) == metadata["size"]
                assert sha256(payload).hexdigest() == metadata["sha256"]
            assert archive.read("data/product_images/" + image.name) == b"fixture image"
            assert archive.read("data/purchase/fixture.xlsx") == b"fixture attachment"
            assert json.loads(archive.read("data/config.json"))["port"] == main.CONFIG["port"]
            saved = tmp_path / "backup.db"
            saved.write_bytes(archive.read("data/inventory.db"))
        with closing(sqlite3.connect(saved)) as con:
            assert con.execute("SELECT image_filename FROM products WHERE id='RC6-FULL'").fetchone()[0] == image.name
    finally:
        _close_project(project, old_cwd)
