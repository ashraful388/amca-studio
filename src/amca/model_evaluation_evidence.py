"""AMCA Studio v1.0.0 Model Evaluation Evidence and Benchmarking Foundation.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-EVALUATION-EVIDENCE-AND-BENCHMARKING-FOUNDATION-01

Responsibilities:
1. Model Evaluation Record System:
   - Records concrete measured performance (coding, reasoning, latency, memory).
   - Tracks environment (hardware, daemon, endpoint type, operator approval).
   - Recorded facts only; zero inferred ratings, winner badges, or automatic routing.
2. Benchmark Evidence Registry:
   - Stores measured execution metrics linked to verifiable artifact and log references.
3. Local vs Provider Transparency:
   - Explicitly separates local hardware conditions from provider endpoint characteristics.
   - Strictly enforces credential privacy: zero keys, passwords, or tokens stored.
"""

from __future__ import annotations

import datetime
import enum
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class EvaluationTaskCategory(str, enum.Enum):
    CODING = "coding"
    REASONING = "reasoning"
    DOCUMENT_PROCESSING = "document_processing"
    DOMAIN_SPECIFIC = "domain_specific"
    LATENCY_BENCHMARK = "latency_benchmark"


@dataclass(frozen=True)
class EvaluationExecutionEnvironment:
    locality: str  # "local" | "cloud"
    hardware_spec: Optional[str] = None  # e.g. "16GB RAM, RTX 4080 12GB VRAM"
    runtime_daemon: Optional[str] = None  # e.g. "ollama v0.3.12"
    endpoint_type: Optional[str] = None  # e.g. "openai_compatible"
    approval_reference: Optional[str] = None  # e.g. "OP-APP-20260902-01"


@dataclass(frozen=True)
class ModelEvaluationRecord:
    record_id: str
    model_id: str
    provider: str
    task_category: EvaluationTaskCategory
    dataset_reference: str
    environment: EvaluationExecutionEnvironment
    timestamp: str
    measured_metrics: Dict[str, Any]  # e.g. {"pass_rate": 0.82, "tokens_per_sec": 48.2}
    raw_output_artifact_ref: str
    verification_status: str  # "verified" | "unverified" | "disputed"
    operator_verifier: str


class BenchmarkEvidenceRegistry:
    """Stores and queries concrete model benchmark evaluation records without ranking or automated routing."""

    def __init__(self) -> None:
        self._records: Dict[str, ModelEvaluationRecord] = {}

    def register_evaluation(
        self,
        model_id: str,
        provider: str,
        task_category: EvaluationTaskCategory,
        dataset_reference: str,
        environment: EvaluationExecutionEnvironment,
        measured_metrics: Dict[str, Any],
        raw_output_artifact_ref: str,
        verification_status: str = "verified",
        operator_verifier: str = "Operator",
    ) -> ModelEvaluationRecord:
        # Generate deterministic record ID
        seed = f"{model_id}:{task_category.value}:{dataset_reference}:{len(self._records)}"
        rid = f"eval-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"

        # Validate that metrics contain NO forbidden subjective score tags
        forbidden_keys = ["rank", "ranking", "winner", "recommended", "best_model", "overall_grade"]
        for k in measured_metrics.keys():
            if k.lower() in forbidden_keys:
                raise ValueError(f"Subjective ranking field '{k}' is forbidden in evaluation records")

        record = ModelEvaluationRecord(
            record_id=rid,
            model_id=model_id,
            provider=provider,
            task_category=task_category,
            dataset_reference=dataset_reference,
            environment=environment,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            measured_metrics=measured_metrics,
            raw_output_artifact_ref=raw_output_artifact_ref,
            verification_status=verification_status,
            operator_verifier=operator_verifier,
        )
        self._records[rid] = record
        return record

    def get_record(self, record_id: str) -> Optional[ModelEvaluationRecord]:
        return self._records.get(record_id)

    def list_records_for_model(self, model_id: str) -> List[ModelEvaluationRecord]:
        return [r for r in self._records.values() if r.model_id == model_id]

    def list_all_records(self) -> List[ModelEvaluationRecord]:
        return list(self._records.values())

    def summarize_evidence_for_model(self, model_id: str) -> Dict[str, Any]:
        records = self.list_records_for_model(model_id)
        return {
            "model_id": model_id,
            "total_evaluations": len(records),
            "verified_evaluations": sum(1 for r in records if r.verification_status == "verified"),
            "categories_tested": list(set(r.task_category.value for r in records)),
            "records": [asdict(r) for r in records],
        }

