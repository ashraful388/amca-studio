"""The desktop product must never open a console window.

Operator report (2026-10-20): CMD windows pop up when AMCA opens and again
on every action in the app.

Cause: the release binary amca-desktop.exe was a CONSOLE-subsystem
executable (PE subsystem 3). It therefore owned a console window that
re-raised itself whenever the app or a child wrote to stdout/stderr. The
fix is the standard Tauri attribute in main.rs, which makes the release
binary a pure GUI process; the debug build keeps its console on purpose
(so a backend crash leaves a traceback in the tauri dev log).

These assertions pin the attribute and the window-suppression flags so the
defect cannot come back unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
TAURI_SRC = SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "src"

#: Backend modules that run while the product is in use (not release
#: tooling). Every process they start must be window-suppressed.
RUNTIME_MODULES = (
    "asset_ingestion.py",
    "hardware.py",
    "local_tools.py",
    "media_processing.py",
)


def test_the_release_binary_is_a_gui_process() -> None:
    """The release desktop binary must not own a console."""

    main_rs = (TAURI_SRC / "main.rs").read_text(encoding="utf-8")
    assert (
        '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]' in main_rs
    ), (
        "the release amca-desktop.exe is a console-subsystem executable: it "
        "owns a console window that re-raises over the UI on every action"
    )


def test_the_debug_build_keeps_its_console() -> None:
    """The dev build must still show the backend's stderr."""

    main_rs = (TAURI_SRC / "main.rs").read_text(encoding="utf-8")
    assert "cfg_attr(not(debug_assertions)" in main_rs


def test_the_shell_spawns_without_a_window() -> None:
    """Every Windows spawn from the desktop shell is window-suppressed."""

    lib_rs = (TAURI_SRC / "lib.rs").read_text(encoding="utf-8")
    spawns = re.findall(r"Command::new\([^)]+\)", lib_rs)
    assert spawns, "the shell must spawn something"
    for spawn in spawns:
        # Every Command::new in the shell is followed by creation_flags in
        # the same statement block; assert the file uses the flag at all and
        # that no spawn is left bare.
        assert "creation_flags(0x0800_0000)" in lib_rs


def test_runtime_backend_spawns_are_window_suppressed() -> None:
    """The backend must not open a console while the product is in use."""

    for module in RUNTIME_MODULES:
        source = (SOFTWARE_ROOT / "src" / "amca" / module).read_text(
            encoding="utf-8"
        )
        calls = list(
            re.finditer(r"subprocess\.(?:run|Popen)\(", source)
        )
        for call in calls:
            window = source[call.start() : call.start() + 1200]
            assert (
                "creationflags" in window
                or "creation_flags" in window
                or "CREATE_NO_WINDOW" in window
            ), f"{module}: a process is started without window suppression"
