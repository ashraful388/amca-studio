"""Current-release regression runner (20BA).

Tooling only; no product/source/packaging changes. Executes the CURRENT-LINE
regression battery for the v1.1.1 integration line and produces a structured
report that:

1. runs only current-line suites (no physical final release artifacts required);
2. classifies the five known historical packaging/preflight suites (20AZ
   triage) as HISTORICAL and never runs them as current evidence;
3. fails CLOSED on any unclassified packaging/preflight-named suite;
4. reports historical exclusions explicitly;
5. reports known placeholder xfails (delegated-subagent 17E5, 17E6 streaming)
   separately from passes, hiding nothing.

A historical suite's PASS is never evidence for current v1.1.1 packaging:
historical suites are excluded structurally (the runner does not execute
them), and the report carries an explicit EXCLUDED_FROM_CURRENT_EVIDENCE
section.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

# The five historical packaging/preflight suites (20AZ triage verdict:
# stale-historical certifiers of the 0.9.0/1.0.0 immutable releases).
HISTORICAL_SUITES = frozenset({
    "test_studio_packaging_and_distribution.py",
    "test_studio_release_package_consistency.py",
    "test_studio_v090_release_packaging_distribution.py",
    "test_studio_v100_final_package_compliance_verification.py",
    "test_studio_v100_final_version_freeze_preflight.py",
})

# Current-line battery (no physical final release artifacts required).
CURRENT_LINE_SUITES = (
    # service / 17E5
    "test_17e5_service_exposure_20ae.py",
    # frozen 20P oracle + 17E4/20W
    "test_17e4_runtime_acceptance_harness.py",
    "test_17e4_governed_loop_20w.py",
    # Agent Runtime / Fabric
    "test_agent_fabric.py",
    "test_agent_runtime.py",
    "test_agent_runtime_batching.py",
    # 13C / 13D
    "test_studio_v101_13c_agent_artifact_governance.py",
    "test_studio_v101_13d_verifying_cancellation.py",
    # observability 13H-13K
    "test_studio_v101_13h_agent_observability_core.py",
    "test_studio_v101_13i_durable_provenance_bindings.py",
    "test_studio_v101_13j_agent_run_tree_api.py",
    "test_studio_v101_13k_run_summary_query_efficiency.py",
    # provider / router / ToolCall
    "test_cross_provider_router.py",
    "test_m4_router_foundation.py",
    "test_toolcall_production_wiring_20d1.py",
    "test_provider_adapter_conformance.py",
    # artifacts DOCX/PPTX/XLSX
    "test_artifacts.py",
    "test_artifact_render.py",
    # canonical M12
    "test_m12_model_planner.py",
    # 20L/20N/20Y packaging/G8 harness tests where present
    "test_packaged_dependency_hardening_20l.py",
    "test_g8_acceptance_harness.py",
    "test_g8_dependency_gate_fail_closed_20y.py",
    # version sentinel
    "test_studio_v100_ps2_canonical_integration_sentinel.py",
    # 20BU: seven release-critical suites created after 20BA, registered so
    # the final converged-tip regression covers the entire v1.1.1
    # current-line surface (17E6 dedicated behavior, zero-cost acceptance
    # harness gates, compliance gate).
    "test_17e6_interactive_sse_service_20as.py",
    "test_final_zero_cost_acceptance_20bf.py",
    "test_final_zero_cost_acceptance_20bo.py",
    "test_final_zero_cost_acceptance_20bq.py",
    "test_final_zero_cost_acceptance_20bqr1.py",
    "test_current_release_compliance_gate_20bh.py",
    "test_current_release_compliance_gate_20bh_r1.py",
    # 21A: release-tooling recovery regression guards (publication-scoped
    # provenance + SBOM/notices manifest binding); hermetic (tmp git repos)
    "test_studio_v112_release_tooling_recovery_21a.py",
    # 22A: clean payload custody recovery guards (canonical payload snapshot,
    # marker-aware cross-format identity, version-bound notices title)
    "test_studio_v113_clean_payload_recovery_22a.py",
    # 24A: defect remediation regression guards (anti-fabrication, governed chat, profile persistence)
    "test_v114_24a_defect_remediation.py",
)

# Packaging/G8 harness suites that are current-line (20L/20N/20Y) but live
# on the packaging line: included WHERE PRESENT, skipped explicitly when the
# canonical checkout does not carry them yet (20AM/20AN not merged).
VETTED_CURRENT_PACKAGING_SUITES = frozenset({
    "test_packaged_dependency_hardening_20l.py",
    "test_g8_acceptance_harness.py",
    "test_g8_dependency_gate_fail_closed_20y.py",
})

# 20BU: vetted current-line tooling/gate suites created after 20BA. They
# are registered so the domain-pattern fail-closed rule (which exists to
# catch UNREGISTERED packaging/preflight suites) does not misclassify them;
# their file presence still follows the where-present contract.
VETTED_CURRENT_TOOLING_SUITES = frozenset({
    "test_17e6_interactive_sse_service_20as.py",
    "test_final_zero_cost_acceptance_20bf.py",
    "test_final_zero_cost_acceptance_20bo.py",
    "test_final_zero_cost_acceptance_20bq.py",
    "test_final_zero_cost_acceptance_20bqr1.py",
    "test_current_release_compliance_gate_20bh.py",
    "test_current_release_compliance_gate_20bh_r1.py",
    # 21A: release-tooling recovery regression guards (publication-scoped
    # provenance + SBOM/notices manifest binding); hermetic (tmp git repos)
    "test_studio_v112_release_tooling_recovery_21a.py",
})

# Any unclassified suite whose name sits in the packaging/preflight domain
# must fail closed: it is neither a vetted current-line suite nor one of the
# five triaged historical suites.
_PACKAGING_DOMAIN_PATTERN = re.compile(
    r"packag|preflight|compliance|distribution|release_", re.IGNORECASE
)

# Known placeholder xfails inside the current-line frozen oracle; reported
# separately, never silently folded into pass counts.
KNOWN_XFAILS = (
    {
        "test_id": (
            "tests/test_17e4_runtime_acceptance_harness.py::"
            "FutureMultiTurnOrchestrationTests::test_future_17e5_delegated_subagent_tool_loop"
        ),
        "classification": "EXPECTED_FAIL_UNTIL_17E5",
        "note": "delegated-subagent placeholder; truthful xfail",
    },
    {
        "test_id": (
            "tests/test_17e4_runtime_acceptance_harness.py::"
            "FutureMultiTurnOrchestrationTests::test_future_17e6_streaming_service_tool_route"
        ),
        "classification": "EXPECTED_FAIL_UNTIL_17E6",
        "note": "17E6 streaming placeholder; truthful xfail pending official 20AV",
    },
)


def classify_suite(suite_name: str) -> str:
    """Classify a suite name without executing it.

    Returns one of: "historical", "current_packaging", "unknown_packaging",
    "current", "unrelated".
    """
    name = Path(suite_name).name
    if name in HISTORICAL_SUITES:
        return "historical"
    if name in VETTED_CURRENT_PACKAGING_SUITES:
        return "current_packaging"
    if name in VETTED_CURRENT_TOOLING_SUITES:
        return "current"
    if _PACKAGING_DOMAIN_PATTERN.search(name):
        return "unknown_packaging"
    if name in CURRENT_LINE_SUITES:
        return "current"
    return "unrelated"


def _run_suite(
    software_root: Path, suite: str, pytest_extra: List[str]
) -> Dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", f"tests/{suite}", "-q", *pytest_extra],
        cwd=str(software_root),
        env={**__import__("os").environ, "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
    )
    return {
        "suite": suite,
        "classification": "current",
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "summary": (proc.stdout.strip().splitlines() or [""])[-1],
    }


def run_current_release_battery(
    software_root: Path,
    pytest_extra: Optional[List[str]] = None,
    executor: Optional[Callable[[Path, str, List[str]], Dict]] = None,
) -> Dict:
    """Execute the current-line battery and return the structured report.

    `executor` is an injection seam for tests; production uses _run_suite.
    Historical suites are never executed. Unclassified packaging-domain
    suites fail closed. Current-line failures propagate as failures.
    """
    software_root = Path(software_root)
    pytest_extra = list(pytest_extra or [])
    run = executor or _run_suite

    current_results: List[Dict] = []
    skipped_absent: List[Dict] = []
    fail_closed: List[Dict] = []

    for suite in CURRENT_LINE_SUITES:
        cls = classify_suite(suite)
        if cls == "current":
            if suite in VETTED_CURRENT_TOOLING_SUITES and not (
                software_root / "tests" / suite
            ).is_file():
                # Where-present contract (same as the packaging group): on
                # a pre-convergence checkout the suite is reported as
                # SKIPPED_ABSENT — explicit, never silent, never evidence.
                skipped_absent.append({
                    "suite": suite,
                    "classification": cls,
                    "status": "SKIPPED_ABSENT",
                    "reason": (
                        "20BU tooling/gate suite not carried by this "
                        "checkout; included where present, never silently"
                    ),
                })
                continue
            current_results.append(run(software_root, suite, pytest_extra))
        elif cls == "current_packaging":
            if (software_root / "tests" / suite).is_file():
                current_results.append(
                    run(software_root, suite, pytest_extra)
                )
            else:
                skipped_absent.append({
                    "suite": suite,
                    "classification": cls,
                    "status": "SKIPPED_ABSENT",
                    "reason": (
                        "current-line packaging suite not carried by this "
                        "checkout (20AM/20AN packaging line unmerged); "
                        "included where present, never silently"
                    ),
                })
        else:
            fail_closed.append({
                "suite": suite,
                "classification": cls,
                "status": "FAIL_CLOSED",
                "reason": (
                    "manifest entry no longer classifies as current-line"
                ),
            })

    report = {
        "runner": "current_release_regression_runner_20ba",
        "canonical_expectation": "integration/v1.1.1",
        "current_line": current_results,
        "skipped_absent": skipped_absent,
        "fail_closed": fail_closed,
        "historical_excluded": [
            {
                "suite": suite,
                "classification": "historical",
                "status": "EXCLUDED_FROM_CURRENT_EVIDENCE",
                "reason": (
                    "20AZ triage: certifies historical 0.9.0/1.0.0 immutable "
                    "releases (hard version pins and/or uncommitted dist "
                    "artifacts); never evidence for current v1.1.1 packaging"
                ),
            }
            for suite in sorted(HISTORICAL_SUITES)
        ],
        "known_xfails": [dict(x) for x in KNOWN_XFAILS],
        "verdict": (
            "PASS"
            if all(r["status"] == "PASS" for r in current_results)
            and not fail_closed
            else "FAIL"
        ),
    }
    return report


def main(argv: List[str]) -> int:
    software_root = Path(__file__).resolve().parents[1]
    report = run_current_release_battery(software_root, pytest_extra=argv)
    import json

    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
