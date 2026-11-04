"""Unit tests for AMCA Studio v1.0.0 Model Selection Workspace & Comparison UX.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-SELECTION-WORKSPACE-AND-COMPARISON-UX-01
"""

import pytest

from amca.model_evaluation_evidence import (
    BenchmarkEvidenceRegistry,
    EvaluationExecutionEnvironment,
    EvaluationTaskCategory,
)
from amca.model_registry_ecosystem import ModelEcosystemManager
from amca.model_selection_workspace import (
    ManualModelSelectionRecord,
    ModelComparisonGrid,
    ModelSelectionManager,
)


def test_build_comparison_matrix_factual_and_zero_ranking():
    model_mgr = ModelEcosystemManager()
    model_mgr.register_model("m1", "Model Alpha", "ollama", "local", "1.0", ["code"], user_notes="Notes 1")
    model_mgr.register_model("m2", "Model Beta", "ollama", "local", "2.0", ["chat"], user_notes="Notes 2")

    bench_mgr = BenchmarkEvidenceRegistry()
    env = EvaluationExecutionEnvironment(locality="local")
    bench_mgr.register_evaluation(
        model_id="m1",
        provider="ollama",
        task_category=EvaluationTaskCategory.CODING,
        dataset_reference="humaneval",
        environment=env,
        measured_metrics={"pass_rate": 0.8},
        raw_output_artifact_ref="art1",
    )

    matrix = ModelComparisonGrid.build_comparison_matrix(["m1", "m2"], model_mgr, bench_mgr)

    assert matrix["total_compared"] == 2
    assert len(matrix["columns"]) == 2
    assert matrix["columns"][0]["model_name"] == "Model Alpha"
    assert matrix["columns"][1]["model_name"] == "Model Beta"

    # Strictly verify absence of rankings
    assert matrix["winner"] is None
    assert matrix["recommended_model"] is None
    assert matrix["ranking"] is None


def test_manual_model_selection_record_creation():
    sel_mgr = ModelSelectionManager()
    rec = sel_mgr.record_manual_selection(
        model_id="m1",
        project_context="Project-Phoenix",
        evidence_references_reviewed=["bench:humaneval", "bench:latency"],
        operator_rationale_notes="Operator verified local code completion satisfies latency SLA",
    )

    assert rec.model_id == "m1"
    assert rec.project_context == "Project-Phoenix"
    assert rec.is_manual_operator_choice is True
    assert "Operator verified" in rec.operator_rationale_notes


def test_manual_selection_requires_rationale_notes():
    sel_mgr = ModelSelectionManager()
    with pytest.raises(ValueError, match="Operator rationale notes are required"):
        sel_mgr.record_manual_selection(
            model_id="m1",
            project_context="P1",
            evidence_references_reviewed=["ref1"],
            operator_rationale_notes="   ",
        )


def test_model_selection_manager_querying():
    sel_mgr = ModelSelectionManager()
    s1 = sel_mgr.record_manual_selection("m1", "ProjA", ["r1"], "Notes A")
    s2 = sel_mgr.record_manual_selection("m2", "ProjB", ["r2"], "Notes B")

    proj_a_list = sel_mgr.list_selections_for_project("ProjA")
    assert len(proj_a_list) == 1
    assert proj_a_list[0].selection_id == s1.selection_id
    assert len(sel_mgr.list_all_selections()) == 2


def test_router_isolation_and_no_automated_fallback():
    sel_mgr = ModelSelectionManager()
    # Confirm no automated fallback routing logic exists on selection manager
    assert not hasattr(sel_mgr, "auto_fallback_model")
    assert not hasattr(sel_mgr, "route_query")
    assert not hasattr(sel_mgr, "select_best_candidate")

