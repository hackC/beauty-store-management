# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project_dir = Path(SPECPATH).resolve()
static_dir = project_dir / "static"
datas = [(str(static_dir), "static")]
binaries = []
hiddenimports = ["multipart", "multipart.multipart", "uvicorn.logging", "uvicorn.lifespan.on", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "openpyxl", "cryptography", "cryptography.hazmat.primitives.serialization", "cryptography.x509"]
for package in ("fastapi", "uvicorn", "openpyxl", "multipart", "pydantic", "starlette", "cryptography"):
    collected = collect_all(package)
    datas += collected[0]
    binaries += collected[1]
    hiddenimports += collected[2]

a = Analysis([str(project_dir / "main.py")], pathex=[str(project_dir)], binaries=binaries, datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="StoreInventoryServer", debug=False, bootloader_ignore_signals=False, strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None, console=False, disable_windowed_traceback=False, argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)
