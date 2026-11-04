import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules


software_root = Path(SPECPATH).parent
sys.path.insert(0, str(software_root / "src"))

hidden_amca = collect_submodules("amca")

a = Analysis(
    [str(software_root / "packaging" / "amca_backend.py")],
    pathex=[str(software_root / "src")],
    binaries=[],
    datas=[(str(software_root / "benchmarks" / "tasks.json"), "benchmarks")],
    # Asset extraction imports pypdf lazily so it remains optional to source
    # readers, but it is a required capability of the standalone desktop build.
    # 28B: every lazily-imported third-party runtime dependency must be
    # bundled so the installed product has the same capabilities as the
    # development tree (artifact conversion, research ingestion, PDF).
    hiddenimports=[
        "pypdf",
        "anthropic",
        "openai",
        "docx",
        "docx.shared",
        "pptx",
        "openpyxl",
    ] + hidden_amca,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="amca-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
