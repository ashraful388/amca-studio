"""Agent Ecosystem and Workflow Template Foundation (AG-AMCA-STUDIO-BETA-B8).

Provides local-first packaging, discovery, validation, and registry for reusable
AMCA agent definitions and workflow templates.
Zero external cloud marketplace dependencies; strictly local and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class AgentPackageDefinition:
    package_id: str
    name: str
    version: str
    author: str
    description: str
    capabilities: tuple[str, ...]
    required_tools: tuple[str, ...]
    compatible_runtimes: tuple[str, ...]
    is_verified: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "required_tools": list(self.required_tools),
            "compatible_runtimes": list(self.compatible_runtimes),
            "is_verified": self.is_verified,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class WorkflowTemplateDefinition:
    template_id: str
    name: str
    version: str
    purpose: str
    required_capabilities: tuple[str, ...]
    recommended_mode: str
    required_models: tuple[str, ...]
    steps_blueprint: tuple[dict[str, Any], ...]
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "version": self.version,
            "purpose": self.purpose,
            "required_capabilities": list(self.required_capabilities),
            "recommended_mode": self.recommended_mode,
            "required_models": list(self.required_models),
            "steps_blueprint": list(self.steps_blueprint),
            "created_at": self.created_at,
        }


def validate_template_safety(
    template: WorkflowTemplateDefinition,
    available_capabilities: Sequence[str],
    available_models: Sequence[str],
) -> tuple[bool, list[str]]:
    """Evaluates whether all requirements for a template are satisfied before user staging."""
    missing: list[str] = []
    avail_caps = set(available_capabilities)
    avail_models = set(available_models)

    for c in template.required_capabilities:
        if c not in avail_caps:
            missing.append(f"Missing capability: '{c}'")

    for m in template.required_models:
        if m not in avail_models:
            missing.append(f"Missing required model/tier: '{m}'")

    if missing:
        return False, missing
    return True, ["All required capabilities and model dependencies are satisfied"]


class AgentEcosystemStore:
    """Local SQLite registry for reusable agent packages and workflow templates."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_packages (
                    package_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def register_package(self, pkg: AgentPackageDefinition) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO agent_packages VALUES (?, ?, ?, ?, ?)
                """,
                (
                    pkg.package_id,
                    pkg.name,
                    pkg.version,
                    json.dumps(pkg.to_dict()),
                    pkg.created_at or _now(),
                ),
            )

    def list_packages(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM agent_packages ORDER BY name ASC, version DESC"
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

    def register_template(self, tmpl: WorkflowTemplateDefinition) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO workflow_templates VALUES (?, ?, ?, ?, ?)
                """,
                (
                    tmpl.template_id,
                    tmpl.name,
                    tmpl.version,
                    json.dumps(tmpl.to_dict()),
                    tmpl.created_at or _now(),
                ),
            )

    def list_templates(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM workflow_templates ORDER BY name ASC, version DESC"
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]

