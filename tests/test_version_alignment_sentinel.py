"""17H2 version-alignment sentinel.

package.json is the canonical release-control version source (versionLabel.ts
is its visible projection). This sentinel fails clearly if any checked-in
version-bearing source drifts from it. No code generation, no new version
subsystem: read + compare only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_UI = SOFTWARE_ROOT / "desktop-ui"
TAURI = DESKTOP_UI / "src-tauri"


def _package_json_version() -> str:
    data = json.loads((DESKTOP_UI / "package.json").read_text(encoding="utf-8"))
    return str(data["version"])


def _version_from_json(path: Path, key: str = "version") -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data[key])


def _cargo_version(path: Path) -> str:
    match = re.search(
        r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match, f"no package version in {path}"
    return match.group(1)


def _pyproject_version() -> str:
    match = re.search(
        r"^version\s*=\s*\"([^\"]+)\"",
        (SOFTWARE_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, "no [project] version in pyproject.toml"
    return match.group(1)


def _dunder_version() -> str:
    match = re.search(
        r'^__version__\s*=\s*"([^"]+)"',
        (SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, "no __version__ in amca/__init__.py"
    return match.group(1)


def _version_label_is_package_driven() -> None:
    text = (DESKTOP_UI / "src" / "versionLabel.ts").read_text(encoding="utf-8")
    assert "../package.json" in text and "APP_VERSION" in text, (
        "versionLabel.ts must stay the package.json-derived projection"
    )


def test_all_version_sources_match_package_json() -> None:
    canonical = _package_json_version()
    _version_label_is_package_driven()
    checks = {
        "desktop-ui/package.json": canonical,
        "versionLabel.ts": f"v{canonical}",
        "src-tauri/tauri.conf.json": _version_from_json(TAURI / "tauri.conf.json"),
        "src-tauri/Cargo.toml": _cargo_version(TAURI / "Cargo.toml"),
        "pyproject.toml": _pyproject_version(),
        "amca/__init__.py": _dunder_version(),
    }
    assert checks["versionLabel.ts"] == f"v{canonical}"
    drift = {
        name: value
        for name, value in checks.items()
        if name != "versionLabel.ts" and value != canonical
    }
    assert not drift, (
        f"version drift vs canonical package.json={canonical}: {drift}"
    )
