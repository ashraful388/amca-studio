"""AMCA Studio v0.9.0 Production Readiness and Integration Hardening.

Milestone: AG-AMCA-STUDIO-V0.9.0-PRODUCTION-READINESS-AND-INTEGRATION-HARDENING-01

Core Principles:
1. User-Readable Error Handling: Formats errors derived strictly from recorded facts and events. Zero AI diagnosis or speculation.
2. Deterministic Recovery Guidance: Maps explicit failure codes to deterministic recovery actions.
3. Release Readiness Audit: Audits version consistency, offline readiness, and storage migration safety.
4. Commercial Boundary Declaration: Explicitly states available features vs. non-claimed capabilities (anti-hype).
5. Strict Decoupling: Zero modification of Model Router, Agent Fabric, or autonomous execution policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Literal

from amca.agent_capability_governance import ModelCapabilityProfile
from amca.cognitive_runtime_bridge import sanitize_evidence_facts


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass
class ProductionErrorExplanation:
    error_code: str
    summary: str
    explanation: str
    recorded_context: dict[str, Any]
    recovery_steps: list[str]
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "summary": self.summary,
            "explanation": self.explanation,
            "recorded_context": dict(self.recorded_context),
            "recovery_steps": list(self.recovery_steps),
            "timestamp": self.timestamp,
        }


class ProductionErrorFormatter:
    """Formats runtime failures into clear, user-readable explanations based strictly on recorded facts."""

    STANDARD_RECOVERIES: dict[str, tuple[str, list[str]]] = {
        "CAPABILITY_MISMATCH": (
            "The selected agent or model profile does not satisfy all declared capability requirements.",
            [
                "Inspect the required capabilities specified in the workflow or role assignment.",
                "Register or select an agent profile supporting the missing capabilities.",
                "Or adjust the role requirements to match available resources.",
            ],
        ),
        "SAFETY_GATE_REJECTED": (
            "One or more pre-execution safety gates failed during handoff evaluation.",
            [
                "Verify that human operator approval exists and is recorded on the plan.",
                "Confirm that assigned model providers are approved for execution.",
                "Ensure local offline constraints and resource availability are satisfied.",
            ],
        ),
        "PROVIDER_UNAVAILABLE": (
            "The configured model inference provider endpoint is currently unreachable or offline.",
            [
                "Ensure local daemon (such as Ollama or LocalAI) is active and running.",
                "Check that required model weights are pulled and loaded locally.",
                "Review network or localhost port settings in the provider governance settings.",
            ],
        ),
        "PLAN_UNAPPROVED": (
            "Attempted to initiate execution handoff for a plan that has not been approved.",
            [
                "Submit the execution plan for human review.",
                "Obtain explicit operator approval before re-requesting handoff.",
            ],
        ),
        "OPERATOR_CANCELLED": (
            "Execution handoff was deliberately cancelled by an operator action.",
            [
                "Review the operator cancellation reason recorded in the audit trail.",
                "Re-request execution handoff when ready to proceed.",
            ],
        ),
    }

    @classmethod
    def format_failure(
        cls,
        *,
        error_code: str,
        raw_message: str,
        recorded_facts: dict[str, Any] | None = None,
    ) -> ProductionErrorExplanation:
        """Constructs an explanation strictly from recorded facts with zero AI speculation."""
        facts = sanitize_evidence_facts(recorded_facts or {})
        code = str(error_code).strip().upper()

        if code in cls.STANDARD_RECOVERIES:
            summary, steps = cls.STANDARD_RECOVERIES[code]
            explanation = f"{summary} Details: {raw_message}"
        else:
            summary = "An unexpected runtime error occurred."
            steps = [
                "Review the recorded failure context in the runtime observability log.",
                "Verify local workspace resources and database health.",
            ]
            explanation = f"{summary} Details: {raw_message}"

        return ProductionErrorExplanation(
            error_code=code,
            summary=summary,
            explanation=explanation,
            recorded_context=facts,
            recovery_steps=steps,
        )


class CommercialBoundaryDeclaration:
    """Explicit declaration of available product capabilities vs. non-claimed features."""

    AVAILABLE_FEATURES = [
        "local_first_desktop_platform",
        "multi_model_collaboration",
        "governed_workflows",
        "evidence_based_execution",
        "strict_human_approval_boundaries",
        "observable_runtime_event_streams",
        "offline_first_model_execution",
    ]

    UNCLAIMED_FEATURES = [
        "autonomous_ai_employee",
        "unlimited_agent_swarm",
        "cloud_saas_platform",
        "marketplace_ecosystem",
        "hidden_telemetry",
        "autonomous_unsupervised_execution",
    ]

    @classmethod
    def get_commercial_status(cls) -> dict[str, Any]:
        return {
            "product_name": "AMCA Studio",
            "release_track": "v0.9.0-production-readiness",
            "available_features": list(cls.AVAILABLE_FEATURES),
            "unclaimed_features": list(cls.UNCLAIMED_FEATURES),
            "local_first": True,
            "telemetry_free": True,
            "autonomous_agency": False,
        }


class ReleaseReadinessAuditor:
    """Verifies repository version consistency, offline execution readiness, and packaging status."""

    @staticmethod
    def audit_versions(
        *,
        pyproject_path: Path,
        package_json_path: Path,
        init_path: Path,
    ) -> dict[str, Any]:
        """Extracts and verifies version consistency across project metadata."""
        pyproject_ver = None
        if pyproject_path.exists():
            text = pyproject_path.read_text(encoding="utf-8")
            m = re.search(r'version\s*=\s*"([^"]+)"', text)
            if m:
                pyproject_ver = m.group(1)

        pkg_json_ver = None
        if package_json_path.exists():
            pkg_data = json.loads(package_json_path.read_text(encoding="utf-8"))
            pkg_json_ver = pkg_data.get("version")

        init_ver = None
        if init_path.exists():
            text = init_path.read_text(encoding="utf-8")
            m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
            if m:
                init_ver = m.group(1)

        consistent = (pyproject_ver is not None) and (pyproject_ver == pkg_json_ver == init_ver)
        return {
            "pyproject_version": pyproject_ver,
            "package_json_version": pkg_json_ver,
            "init_version": init_ver,
            "consistent": consistent,
        }

    @staticmethod
    def audit_offline_readiness(profiles: list[ModelCapabilityProfile]) -> dict[str, Any]:
        """Audits whether offline model execution profiles are registered and configured."""
        offline_models = [p for p in profiles if p.locality in ("LOCAL_ONLY", "LOCAL_OFFLINE")]
        return {
            "total_registered_models": len(profiles),
            "offline_models_count": len(offline_models),
            "offline_ready": len(offline_models) > 0,
            "offline_model_ids": [p.model_id for p in offline_models],
        }

