"""AMCA Studio v1.0.0 Model Selection Workspace & Comparison UX.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-SELECTION-WORKSPACE-AND-COMPARISON-UX-01

Responsibilities:
1. Model Comparison Workspace:
   - Side-by-side factual comparison across model identity, locality, capability evidence, and measured benchmarks.
   - Strictly factual; zero synthetic scores, winner declarations, or automatic rankings.
2. Manual Model Selection Records:
   - Captures operator's explicit model choices with project context, evidence reviewed, and operator notes.
   - Enforces manual-only selection: automated silent fallback is forbidden without operator confirmation.
3. Selection Transparency Panel:
   - Displays operator-provided rationale notes and evidence provenance; zero AI-generated explanations.
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from amca.model_evaluation_evidence import BenchmarkEvidenceRegistry, ModelEvaluationRecord
from amca.model_registry_ecosystem import ModelEcosystemManager, ModelRegistryEntry


@dataclass(frozen=True)
class ManualModelSelectionRecord:
    selection_id: str
    model_id: str
    project_context: str
    timestamp: str
    evidence_references_reviewed: List[str]
    operator_rationale_notes: str
    provider_approval_context: Optional[str] = None
    is_manual_operator_choice: bool = True


class ModelComparisonGrid:
    """Builds side-by-side comparison matrices across models and empirical benchmark evidence."""

    @staticmethod
    def build_comparison_matrix(
        model_ids: List[str],
        registry: ModelEcosystemManager,
        benchmark_registry: BenchmarkEvidenceRegistry,
    ) -> Dict[str, Any]:
        columns: List[Dict[str, Any]] = []

        for mid in model_ids:
            model_entry = registry.get_model(mid)
            if not model_entry:
                continue

            benchmarks = benchmark_registry.list_records_for_model(mid)

            col = {
                "model_id": model_entry.model_id,
                "model_name": model_entry.model_name,
                "provider": model_entry.provider,
                "locality": model_entry.locality,
                "version": model_entry.version,
                "availability_state": model_entry.availability_state,
                "verified_capabilities": model_entry.verified_capabilities,
                "unknown_capabilities": model_entry.unknown_capabilities,
                "user_notes": model_entry.user_notes,
                "measured_benchmarks": [
                    {
                        "task_category": b.task_category.value,
                        "dataset_reference": b.dataset_reference,
                        "metrics": b.measured_metrics,
                        "environment_locality": b.environment.locality,
                        "verification_status": b.verification_status,
                    }
                    for b in benchmarks
                ],
            }
            columns.append(col)

        return {
            "total_compared": len(columns),
            "columns": columns,
            # Explicitly assert no ranking/winner attributes
            "winner": None,
            "recommended_model": None,
            "ranking": None,
        }


class ModelSelectionManager:
    """Manages explicit human-controlled model selections for projects and workspaces."""

    def __init__(self) -> None:
        self._selections: Dict[str, ManualModelSelectionRecord] = {}

    def record_manual_selection(
        self,
        model_id: str,
        project_context: str,
        evidence_references_reviewed: List[str],
        operator_rationale_notes: str,
        provider_approval_context: Optional[str] = None,
    ) -> ManualModelSelectionRecord:
        if not operator_rationale_notes.strip():
            raise ValueError("Operator rationale notes are required for manual model selection")

        seed = f"{model_id}:{project_context}:{len(self._selections)}"
        sid = f"sel-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"

        rec = ManualModelSelectionRecord(
            selection_id=sid,
            model_id=model_id,
            project_context=project_context,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            evidence_references_reviewed=evidence_references_reviewed,
            operator_rationale_notes=operator_rationale_notes,
            provider_approval_context=provider_approval_context,
            is_manual_operator_choice=True,
        )
        self._selections[sid] = rec
        return rec

    def get_selection(self, selection_id: str) -> Optional[ManualModelSelectionRecord]:
        return self._selections.get(selection_id)

    def list_selections_for_project(self, project_context: str) -> List[ManualModelSelectionRecord]:
        return [s for s in self._selections.values() if s.project_context == project_context]

    def list_all_selections(self) -> List[ManualModelSelectionRecord]:
        return list(self._selections.values())

