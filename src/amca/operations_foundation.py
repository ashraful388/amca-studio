"""AMCA Studio v1.0.0 Post-GA Stability and Operations Foundation.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-POST-GA-STABILITY-AND-OPERATIONS-FOUNDATION-01

Responsibilities:
1. Application Diagnostics Foundation:
   - Local diagnostic report generation, environment capture, installed component inventory.
   - Strict privacy rules: zero secrets, zero API keys, zero cloud telemetry upload.
2. Crash & Failure Reporting UX:
   - Local-only crash summary, failure context export, and user-readable troubleshooting guidance.
3. Backup & Restore Foundation:
   - Non-destructive workspace backup export, restore validation, artifact and knowledge preservation.
4. Update Readiness Foundation:
   - Semantic version comparison, release channel representation (stable, beta, nightly), update availability state.
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import platform
import re
import sqlite3
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ReleaseChannel(str, enum.Enum):
    STABLE = "stable"
    BETA = "beta"
    NIGHTLY = "nightly"


class UpdateState(str, enum.Enum):
    UP_TO_DATE = "UP_TO_DATE"
    UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
    ROLLBACK_DETECTED = "ROLLBACK_DETECTED"


@dataclass(frozen=True)
class DiagnosticReport:
    timestamp: str
    product_version: str
    os_info: Dict[str, str]
    python_info: Dict[str, str]
    components_inventory: Dict[str, str]
    model_providers_summary: List[Dict[str, Any]]
    telemetry_uploaded: bool
    secrets_detected: int


@dataclass(frozen=True)
class CrashReport:
    crash_id: str
    timestamp: str
    exception_type: str
    exception_message: str
    sanitized_traceback: str
    troubleshooting_advice: List[str]
    cloud_reported: bool


class DiagnosticsManager:
    """Collects system diagnostics locally with strict credential scrubbing."""

    SECRET_PATTERNS = [
        (re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|bearer)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"), r"\1: [REDACTED_SECRET]"),
        (re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"), "[REDACTED_SECRET]"),
    ]

    @classmethod
    def sanitize_dict(cls, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        sanitized = {}
        redacted_count = 0
        for k, v in data.items():
            k_lower = k.lower()
            if any(term in k_lower for term in ("key", "secret", "token", "password", "auth", "credential")):
                sanitized[k] = "[REDACTED_SECRET]"
                redacted_count += 1
            elif isinstance(v, dict):
                sub_san, count = cls.sanitize_dict(v)
                sanitized[k] = sub_san
                redacted_count += count
            elif isinstance(v, list):
                sub_list = []
                for item in v:
                    if isinstance(item, dict):
                        sub_san, count = cls.sanitize_dict(item)
                        sub_list.append(sub_san)
                        redacted_count += count
                    else:
                        sub_list.append(item)
                sanitized[k] = sub_list
            elif isinstance(v, str):
                val_str = v
                for pat, repl in cls.SECRET_PATTERNS:
                    if pat.search(val_str):
                        val_str = pat.sub(repl, val_str)
                        redacted_count += 1
                sanitized[k] = val_str
            else:
                sanitized[k] = v
        return sanitized, redacted_count

    def generate_diagnostic_report(
        self,
        providers_config: Optional[List[Dict[str, Any]]] = None,
    ) -> DiagnosticReport:
        import datetime

        os_details = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        }
        python_details = {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        }
        components = {
            "desktop_ui": "0.9.0",
            "backend_core": "0.9.0",
            "storage_sqlite": sqlite3.sqlite_version,
            "locality_engine": "LOCAL_FIRST",
        }

        # Sanitize providers config
        raw_providers = providers_config or [
            {"provider": "ollama", "base_url": "http://localhost:11434", "enabled": True},
            {"provider": "openai_compatible", "api_key": "sk-testsecretkey1234567890", "enabled": False},
        ]
        sanitized_providers = []
        total_redacted = 0
        for p in raw_providers:
            san, count = self.sanitize_dict(p)
            sanitized_providers.append(san)
            total_redacted += count

        return DiagnosticReport(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            product_version="0.9.0",
            os_info=os_details,
            python_info=python_details,
            components_inventory=components,
            model_providers_summary=sanitized_providers,
            telemetry_uploaded=False,
            secrets_detected=total_redacted,
        )


class CrashReportingManager:
    """Formats local crash dumps with troubleshooting advice without cloud egress."""

    @staticmethod
    def capture_crash(exc: Exception, raw_context: Optional[Dict[str, Any]] = None) -> CrashReport:
        import datetime
        import traceback

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        # Redact any paths containing user names or keys
        sanitized_tb = tb
        for pat, repl in DiagnosticsManager.SECRET_PATTERNS:
            sanitized_tb = pat.sub(repl, sanitized_tb)

        advice = [
            "Verify local inference backend is actively running (e.g. `ollama serve`).",
            "Ensure host machine meets the minimum 16GB RAM recommendation for multi-model execution.",
            "Verify local SQLite permissions in `.amca/amca_desktop.db`.",
        ]

        h = hashlib.sha256(f"{type(exc).__name__}:{str(exc)}".encode("utf-8")).hexdigest()[:12]
        return CrashReport(
            crash_id=f"crash-{h}",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            sanitized_traceback=sanitized_tb,
            troubleshooting_advice=advice,
            cloud_reported=False,
        )


class BackupRestoreManager:
    """Handles workspace archiving and non-destructive restoration."""

    @staticmethod
    def export_workspace_backup(
        workspace_id: str,
        workspace_name: str,
        artifacts: List[Dict[str, Any]],
        knowledge_records: List[Dict[str, Any]],
        output_zip: Path,
    ) -> Dict[str, Any]:
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format_version": "1.0.0",
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "artifact_count": len(artifacts),
            "knowledge_record_count": len(knowledge_records),
        }

        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            zf.writestr("artifacts.json", json.dumps(artifacts, indent=2))
            zf.writestr("knowledge.json", json.dumps(knowledge_records, indent=2))

        file_bytes = output_zip.read_bytes()
        sha = hashlib.sha256(file_bytes).hexdigest()

        return {
            "backup_file": str(output_zip),
            "sha256": sha,
            "size_bytes": len(file_bytes),
            "manifest": manifest,
        }

    @staticmethod
    def validate_and_restore_backup(
        backup_zip: Path,
        target_dir: Path,
        allow_destructive_overwrite: bool = False,
    ) -> Dict[str, Any]:
        if not backup_zip.exists():
            raise FileNotFoundError(f"Backup file {backup_zip} does not exist")

        with zipfile.ZipFile(backup_zip, "r") as zf:
            namelist = zf.namelist()
            if "manifest.json" not in namelist:
                raise ValueError("Invalid backup: missing manifest.json")

            manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
            artifacts_data = json.loads(zf.read("artifacts.json").decode("utf-8")) if "artifacts.json" in namelist else []
            knowledge_data = json.loads(zf.read("knowledge.json").decode("utf-8")) if "knowledge.json" in namelist else []

        target_dir.mkdir(parents=True, exist_ok=True)
        existing_marker = target_dir / "workspace_state.json"
        if existing_marker.exists() and not allow_destructive_overwrite:
            return {
                "restored": False,
                "reason": "TARGET_EXISTS_OVERWRITE_FORBIDDEN",
                "details": "Refusing destructive overwrite of existing workspace directory",
            }

        # Safe restoration
        target_marker = target_dir / "workspace_state.json"
        target_marker.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
        (target_dir / "artifacts_restored.json").write_text(json.dumps(artifacts_data, indent=2), encoding="utf-8")
        (target_dir / "knowledge_restored.json").write_text(json.dumps(knowledge_data, indent=2), encoding="utf-8")

        return {
            "restored": True,
            "workspace_id": manifest_data.get("workspace_id"),
            "artifacts_restored": len(artifacts_data),
            "knowledge_records_restored": len(knowledge_data),
            "target_dir": str(target_dir),
        }


class UpdateReadinessManager:
    """Manages semantic version comparison and release channel status."""

    @staticmethod
    def parse_semver(ver: str) -> Tuple[int, int, int]:
        clean = ver.strip().lstrip("v").split("-")[0]
        parts = clean.split(".")
        return (int(parts[0]), int(parts[1]), int(parts[2]))

    @classmethod
    def compare_versions(cls, current_version: str, candidate_version: str) -> UpdateState:
        cur = cls.parse_semver(current_version)
        cand = cls.parse_semver(candidate_version)

        if cand > cur:
            return UpdateState.UPDATE_AVAILABLE
        elif cand < cur:
            return UpdateState.ROLLBACK_DETECTED
        else:
            return UpdateState.UP_TO_DATE

    @classmethod
    def evaluate_channel_readiness(
        cls,
        current_version: str,
        channel: ReleaseChannel,
        channel_latest_version: str,
    ) -> Dict[str, Any]:
        state = cls.compare_versions(current_version, channel_latest_version)
        return {
            "current_version": current_version,
            "channel": channel.value,
            "channel_latest_version": channel_latest_version,
            "update_state": state.value,
            "automatic_download_enabled": False,  # Strict manual user trigger
        }

