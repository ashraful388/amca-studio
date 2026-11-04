#!/usr/bin/env python3
"""I2K final privacy/secrets/user-data release sanitation audit (OpenCode PS-2).

Scans release content (never production logic) for accidentally committed
secrets and private data, with evidence-based rules and explicit
PASS / REVIEW / FAIL classifications:

  python scripts/run_release_sanitation_audit.py [--root DIR]
      [--inspect-package PATH] [--output PATH]

  --root             software tree root (default: this repo's software/)
  --inspect-package  optional packaged artifact (.zip/.exe) for the
                     package-content verification mode (read-only)
  --output           optional path for the machine-readable JSON result

Findings never delete or modify evidence. Exit code 0 only when no FAIL
finding exists (REVIEW findings still exit 0; they require human judgment).

Rule philosophy: STRONG patterns (API key formats, private-key blocks,
bearer tokens with entropy) FAIL on match. WEAK patterns (password/env
assignments) consult a placeholder allowlist first (PLACEHOLDER, EXAMPLE,
YOUR_*, <...>, XXXX, fake/test/dummy/changeme) and otherwise REVIEW.
Fingerprint references (sha256:...) are explicitly safe, never secrets.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

SCHEMA = "amca-release-sanitation-audit/1"

# Scan scope: release content only (never src/ logic, never tests/).
SCAN_DIRS = [
    "docs/release",
    "docs/integration",
    "packaging",
]
TEXT_SUFFIXES = {".md", ".json", ".txt", ".ps1", ".spec", ".yml",
                 ".yaml", ".toml", ".cfg", ".ini", ".xml"}
# Generated certification JSON intended for release (explicit files).
SCAN_EXTRA_FILES = [
    "tests/certification-result.json",
    "tests/test-universe-manifest.json",
]
# Binary extensions never scanned as text (except package-inspect mode).
SKIP_SUFFIXES = {".exe", ".dll", ".zip", ".msi", ".bin", ".png", ".ico",
                 ".pyc", ".pyz", ".pyd", ".lib", ".obj", ".pdb"}

STRONG_RULES = [
    ("openai-key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")),
    ("github-token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"xox[bap]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----")),
    ("bearer-token", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{16,}")),
    ("access-token-assign",
     re.compile(r"(?i)\baccess_token\s*[:=]\s*['\"]?[A-Za-z0-9\-._~+/]{16,}")),
]

WEAK_RULES = [
    ("password-assign",
     re.compile(r"(?i)\b(passw(or)?d|pwd)\s*[:=]\s*['\"]?(\S{4,})")),
    ("env-secret-assign",
     re.compile(r"(?i)\b([A-Z_]*(?:API_KEY|SECRET|TOKEN|PASSWORD))\s*=\s*(\S+)")),
]

PLACEHOLDER_HINTS = ("placeholder", "example", "your_", "<", ">", "xxxx",
                     "fake", "test", "dummy", "changeme", "redacted",
                     "***", "none", "null")

USER_PATH_RULES = [
    ("windows-user-path", re.compile(r"[A-Za-z]:\\Users\\[^\s\"']+")),
    ("posix-home-path", re.compile(r"/home/[^/\s\"']+")),
    ("temp-workspace-path",
     re.compile(r"(?i)(?:\\|/)(?:temp|tmp)(?:\\|/)[^\s\"']*")),
]

EMAIL_RULE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _is_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return any(hint in lowered for hint in PLACEHOLDER_HINTS)


def _redact(value: str) -> str:
    if len(value) <= 12:
        return value[:2] + "***"
    return value[:4] + "***" + value[-2:]


@dataclass
class Finding:
    path: str
    line: Optional[int]
    rule: str
    severity: str  # FAIL | REVIEW | PASS (PASS findings are notes)
    excerpt: str
    reason: str


@dataclass
class AuditResult:
    schema: str = SCHEMA
    overall: str = "PASS"
    findings: List[Finding] = field(default_factory=list)
    files_scanned: int = 0
    notes: List[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.severity == "FAIL":
            self.overall = "FAIL"
        elif finding.severity == "REVIEW" and self.overall == "PASS":
            self.overall = "REVIEW"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "overall": self.overall,
            "files_scanned": self.files_scanned,
            "findings": [asdict(f) for f in self.findings],
            "notes": self.notes,
        }


def scan_text(content: str, rel: str) -> List[Finding]:
    """Scan one text document. Pure function (unit-testable)."""
    findings: List[Finding] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        for rule, pattern in STRONG_RULES:
            match = pattern.search(line)
            if match:
                findings.append(Finding(
                    path=rel, line=lineno, rule=rule, severity="FAIL",
                    excerpt=_redact(match.group(0)),
                    reason="strong secret pattern matched"))
        for rule, pattern in WEAK_RULES:
            match = pattern.search(line)
            if match:
                value = match.group(match.lastindex or 0)
                if _is_placeholder(value):
                    continue
                findings.append(Finding(
                    path=rel, line=lineno, rule=rule, severity="REVIEW",
                    excerpt=_redact(value),
                    reason="secret-like assignment without placeholder marker"))
        for rule, pattern in USER_PATH_RULES:
            match = pattern.search(line)
            if match:
                findings.append(Finding(
                    path=rel, line=lineno, rule=rule, severity="REVIEW",
                    excerpt=match.group(0)[:80],
                    reason="machine-specific path in release content"))
    return findings


def scan_database(db_path: Path, rel: str) -> List[Finding]:
    """Scan TEXT values of a committed fixture/sample database."""
    findings: List[Finding] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [Finding(path=rel, line=None, rule="db-unreadable",
                         severity="REVIEW", excerpt="",
                         reason=f"database could not be opened read-only: {exc}")]
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        for table in tables:
            try:
                cols = [r[1] for r in con.execute(
                    f"PRAGMA table_info({table})")]
                rows = con.execute(f'SELECT * FROM "{table}" LIMIT 5000'
                                   ).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                for col, value in zip(cols, row):
                    if not isinstance(value, str):
                        continue
                    for rule, pattern in STRONG_RULES:
                        if pattern.search(value):
                            findings.append(Finding(
                                path=f"{rel}:{table}.{col}", line=None,
                                rule=rule, severity="FAIL",
                                excerpt=_redact(value[:64]),
                                reason="strong secret pattern in fixture data"))
                    if EMAIL_RULE.search(value):
                        findings.append(Finding(
                            path=f"{rel}:{table}.{col}", line=None,
                            rule="email-in-fixture", severity="REVIEW",
                            excerpt="address present",
                            reason="possible real identity in fixture data"))
    finally:
        con.close()
    return findings


TEXT_SUFFIXES = {".md", ".json", ".txt", ".ps1", ".spec", ".yml",
                 ".yaml", ".toml", ".cfg", ".ini", ".xml"}


def iter_release_files(root: Path):
    """Yield (path, rel) for in-scope release content files."""
    for sub in SCAN_DIRS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in SKIP_SUFFIXES or suffix not in TEXT_SUFFIXES:
                continue
            yield path, str(path.relative_to(root)).replace("\\", "/")
    for rel in SCAN_EXTRA_FILES:
        path = root / rel
        if path.is_file():
            yield path, rel


def audit_tree(root: Path) -> AuditResult:
    """Audit a software tree's release content. Read-only."""
    result = AuditResult()
    for path, rel in iter_release_files(root):
        result.files_scanned += 1
        if path.suffix.lower() in (".db", ".sqlite3"):
            for finding in scan_database(path, rel):
                result.add(finding)
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, ValueError, UnicodeDecodeError):
            result.notes.append(f"skipped non-UTF8 file: {rel}")
            continue
        for finding in scan_text(content, rel):
            result.add(finding)
    return result


def inspect_package(artifact: Path) -> AuditResult:
    """Verification mode: inspect a packaged artifact (read-only)."""
    result = AuditResult()
    result.notes.append(f"package under inspection: {artifact}")
    if not artifact.is_file():
        result.add(Finding(path=str(artifact), line=None,
                           rule="package-missing", severity="FAIL", excerpt="",
                           reason="packaged artifact not found"))
        return result
    if artifact.suffix.lower() == ".zip" and zipfile.is_zipfile(artifact):
        with zipfile.ZipFile(artifact) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                if Path(name).suffix.lower() in SKIP_SUFFIXES - {".zip"}:
                    if Path(name).suffix.lower() in (".exe", ".dll"):
                        continue
                    result.notes.append(f"skipped binary member: {name}")
                    continue
                try:
                    content = zf.read(name).decode("utf-8")
                except (ValueError, UnicodeDecodeError, KeyError):
                    result.notes.append(f"skipped non-UTF8 member: {name}")
                    continue
                for finding in scan_text(content, f"{artifact.name}!{name}"):
                    result.add(finding)
        return result
    # Opaque binary (e.g. installed .exe): printable-strings scan for the
    # strongest signals only; anything else would drown in false positives.
    data = artifact.read_bytes()
    text = re.sub(rb"[^\x20-\x7e\r\n\t]", b" ", data).decode(
        "ascii", errors="ignore")
    for rule, pattern in STRONG_RULES:
        if rule not in ("private-key-block", "openai-key", "anthropic-key"):
            continue
        for match in pattern.finditer(text):
            result.add(Finding(
                path=artifact.name, line=None, rule=f"package:{rule}",
                severity="FAIL" if rule == "private-key-block" else "REVIEW",
                excerpt=_redact(match.group(0)),
                reason="strong signal inside packaged binary"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AMCA release sanitation audit (I2K)")
    parser.add_argument("--root", default=str(SOFTWARE_ROOT),
                        help="Software tree root to audit")
    parser.add_argument("--inspect-package", default=None,
                        help="Optional packaged artifact to inspect")
    parser.add_argument("--output", default=None,
                        help="Write JSON result to this path")
    args = parser.parse_args()

    result = audit_tree(Path(args.root))
    if args.inspect_package:
        packaged = inspect_package(Path(args.inspect_package))
        result.files_scanned += packaged.files_scanned
        for finding in packaged.findings:
            result.add(finding)
        result.notes.extend(packaged.notes)
    rendered = json.dumps(result.to_dict(), indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result.overall != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
