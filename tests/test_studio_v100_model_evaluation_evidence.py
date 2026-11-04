"""Unit tests for AMCA Studio v1.0.0 Model Evaluation Evidence & Benchmarking.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-EVALUATION-EVIDENCE-AND-BENCHMARKING-FOUNDATION-01
"""

import pytest

from amca.model_evaluation_evidence import (
    BenchmarkEvidenceRegistry,
    EvaluationExecutionEnvironment,
    EvaluationTaskCategory,
    ModelEvaluationRecord,
)


def test_model_evaluation_record_creation_and_provenance():
    registry = BenchmarkEvidenceRegistry()
    env = EvaluationExecutionEnvironment(
        locality="local",
        hardware_spec="32GB RAM, RTX 4090",
        runtime_daemon="ollama v0.3.12",
    )
    rec = registry.register_evaluation(
        model_id="local-qwen-7b",
        provider="ollama",
        task_category=EvaluationTaskCategory.CODING,
        dataset_reference="datasets/eval/humaneval_subset_50.jsonl",
        environment=env,
        measured_metrics={"pass_at_1": 0.74, "tokens_per_sec": 52.1},
        raw_output_artifact_ref="artifacts/eval_runs/run_01_qwen7b.json",
        verification_status="verified",
        operator_verifier="Operator Senior QA",
    )

    assert rec.model_id == "local-qwen-7b"
    assert rec.task_category == EvaluationTaskCategory.CODING
    assert rec.measured_metrics["pass_at_1"] == 0.74
    assert rec.environment.locality == "local"
    assert rec.verification_status == "verified"


def test_benchmark_registry_rejects_subjective_ranking_metrics():
    registry = BenchmarkEvidenceRegistry()
    env = EvaluationExecutionEnvironment(locality="local")

    with pytest.raises(ValueError, match="Subjective ranking field 'ranking' is forbidden"):
        registry.register_evaluation(
            model_id="m1",
            provider="ollama",
            task_category=EvaluationTaskCategory.REASONING,
            dataset_reference="ref1",
            environment=env,
            measured_metrics={"accuracy": 0.9, "ranking": 1},
            raw_output_artifact_ref="ref_out",
        )


def test_local_vs_provider_environment_separation():
    registry = BenchmarkEvidenceRegistry()

    # Local environment
    local_env = EvaluationExecutionEnvironment(
        locality="local",
        hardware_spec="16GB Unified RAM, M2 Max",
        runtime_daemon="ollama",
    )
    r_local = registry.register_evaluation(
        model_id="m-local",
        provider="ollama",
        task_category=EvaluationTaskCategory.LATENCY_BENCHMARK,
        dataset_reference="latency_prompt_100.txt",
        environment=local_env,
        measured_metrics={"latency_ms": 195},
        raw_output_artifact_ref="out1",
    )
    assert r_local.environment.locality == "local"
    assert r_local.environment.endpoint_type is None

    # Provider environment
    provider_env = EvaluationExecutionEnvironment(
        locality="cloud",
        endpoint_type="openai_compatible",
        approval_reference="OP-APP-2026-CLOUD-01",
    )
    r_cloud = registry.register_evaluation(
        model_id="m-cloud",
        provider="azure_openai",
        task_category=EvaluationTaskCategory.LATENCY_BENCHMARK,
        dataset_reference="latency_prompt_100.txt",
        environment=provider_env,
        measured_metrics={"latency_ms": 420},
        raw_output_artifact_ref="out2",
    )
    assert r_cloud.environment.locality == "cloud"
    assert r_cloud.environment.approval_reference == "OP-APP-2026-CLOUD-01"


def test_summarize_evidence_for_model_aggregates_without_ranking():
    registry = BenchmarkEvidenceRegistry()
    env = EvaluationExecutionEnvironment(locality="local")

    registry.register_evaluation(
        model_id="deepseek-r1",
        provider="ollama",
        task_category=EvaluationTaskCategory.CODING,
        dataset_reference="ref1",
        environment=env,
        measured_metrics={"score": 0.8},
        raw_output_artifact_ref="out1",
    )
    registry.register_evaluation(
        model_id="deepseek-r1",
        provider="ollama",
        task_category=EvaluationTaskCategory.REASONING,
        dataset_reference="ref2",
        environment=env,
        measured_metrics={"score": 0.88},
        raw_output_artifact_ref="out2",
    )

    summary = registry.summarize_evidence_for_model("deepseek-r1")
    assert summary["model_id"] == "deepseek-r1"
    assert summary["total_evaluations"] == 2
    assert set(summary["categories_tested"]) == {"coding", "reasoning"}
    assert "winner" not in summary
    assert "rank" not in summary


def test_registry_zero_router_mutations():
    registry = BenchmarkEvidenceRegistry()
    assert not hasattr(registry, "select_model")
    assert not hasattr(registry, "route_query")
    assert not hasattr(registry, "rank_candidates")

