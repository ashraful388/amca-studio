"""One-round real Tabitoken plus local-Ollama Joint validation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from amca.domain import (
    CollaborationMode,
    ComplexityLevel,
    ComputePolicy,
    GoalContract,
    GoalCriterion,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from amca.kernel import CollaborationKernel, KernelConfig
from amca.models import OllamaAdapter, TabitokenAdapter
from amca.state import StateManager
from amca.storage import SQLiteStore


TABITOKEN_MODELS = (
    ("claude-opus-4-8", "TABITOKEN_OPUS_4_8_API_KEY", 0.50),
    ("claude-opus-4-8-thinking", "TABITOKEN_OPUS_4_8_THINKING_API_KEY", 0.50),
    ("claude-opus-5", "TABITOKEN_OPUS_5_API_KEY", 0.80),
    ("claude-opus-5-thinking", "TABITOKEN_OPUS_5_THINKING_API_KEY", 0.80),
)
AUTHORIZED_CLOUD_COST_USD = 2.60
MAX_OUTPUT_TOKENS = 128
LOCAL_MODEL = "qwen3:1.7b"
GOAL = (
    "Find the smallest positive integer x satisfying x ≡ 2 (mod 3), "
    "x ≡ 3 (mod 5), and x ≡ 2 (mod 7). State x and independently "
    "verify all three residues."
)


class _JsonOllamaAdapter(OllamaAdapter):
    """Disable Qwen thinking text and require JSON from the local participant."""

    def _payload(self, request, *, stream: bool):
        payload = super()._payload(request, stream=stream)
        payload["think"] = False
        payload["format"] = "json"
        return payload


def _cloud_adapters() -> tuple[TabitokenAdapter, ...]:
    missing = [variable for _, variable, _ in TABITOKEN_MODELS if not os.environ.get(variable)]
    if missing:
        raise RuntimeError(
            "missing process-local Tabitoken credentials: " + ", ".join(missing)
        )
    return tuple(
        TabitokenAdapter(
            model=model,
            api_key_env=variable,
            price_per_request_usd=price,
            timeout=120.0,
        )
        for model, variable, price in TABITOKEN_MODELS
    )


def _state(project_id: str) -> ProjectState:
    criterion = GoalCriterion(
        description=(
            "All required participants return parseable contributions for the "
            "same Chinese-remainder focus."
        )
    )
    return ProjectState(
        project_id=project_id,
        goal=GOAL,
        goal_contract=GoalContract(
            goal=GOAL,
            criteria=[criterion],
            max_iterations=1,
            max_runtime_seconds=180.0,
            max_api_cost_usd=AUTHORIZED_CLOUD_COST_USD,
        ),
        current_focus=GOAL,
        active_subproblem=GOAL,
        complexity=ComplexityLevel.HIGH,
        privacy_mode=PrivacyMode.FULL_FRONTIER,
        compute_policy=ComputePolicy.MAXIMUM_INTELLIGENCE,
    )


async def run_joint_smoke(*, database: Path, output: Path) -> dict[str, Any]:
    cloud = _cloud_adapters()
    local = _JsonOllamaAdapter(
        model=LOCAL_MODEL,
        adapter_id=f"ollama:{LOCAL_MODEL}:r3",
        timeout=120.0,
    )
    adapters = (*cloud, local)
    project_id = "r3-tabitoken-joint-" + uuid4().hex[:12]
    with SQLiteStore(database) as store:
        manager = StateManager(_state(project_id), store=store)
        manager.save()
        kernel = CollaborationKernel(
            manager,
            adapters,
            config=KernelConfig(
                max_rounds=1,
                max_staleness_versions=1_000,
                model_timeout_seconds=120.0,
                rebase_stale_responses=False,
                max_output_tokens_per_call=MAX_OUTPUT_TOKENS,
            ),
        )
        result = await kernel.run(
            mode=CollaborationMode.JOINT,
            sync_mode=SyncMode.HARD,
        )

    cloud_calls = [call for call in result.call_records if call.adapter_id.startswith("tabitoken:")]
    local_calls = [call for call in result.call_records if call.adapter_id.startswith("ollama:")]
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "goal": GOAL,
        "mode": CollaborationMode.JOINT.value,
        "sync_mode": SyncMode.HARD.value,
        "round_limit": 1,
        "rounds_completed": result.rounds_completed,
        "authorized_cloud_cost_usd": AUTHORIZED_CLOUD_COST_USD,
        "accounted_cloud_cost_usd": sum(call.cost_usd for call in cloud_calls),
        "cloud_request_count": len(cloud_calls),
        "local_request_count": len(local_calls),
        "requested_max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
        "provider_reported_output_exceeded_request": any(
            call.output_tokens > MAX_OUTPUT_TOKENS for call in cloud_calls
        ),
        "automatic_retries": 0,
        "rebase_enabled": False,
        "synchronization_passed": result.synchronization_passed,
        "stop_reason": result.stop_reason.value,
        "calls": [
            {
                "adapter_id": call.adapter_id,
                "status": call.status,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "cost_usd": call.cost_usd,
                "elapsed_seconds": call.elapsed_seconds,
                "summary": call.summary,
                "staleness": call.staleness.value,
                "rebase_calls": call.rebase_calls,
                "usage_uncertain": call.usage_uncertain,
                "error": call.error,
            }
            for call in result.call_records
        ],
        "state_version": result.state.version,
        "cognitive_revision": result.state.cognitive_revision,
        "accepted_facts": list(result.state.accepted_facts),
        "open_questions": list(result.state.open_questions),
        "unresolved_disagreements": [
            disagreement.to_dict() for disagreement in result.state.unresolved_disagreements
        ],
        "credential_persisted": False,
    }
    if len(cloud_calls) != 4 or len(local_calls) != 1:
        report["status"] = "failed_call_count"
    elif any(call.rebase_calls for call in result.call_records):
        report["status"] = "failed_rebase_guard"
    elif report["accounted_cloud_cost_usd"] > AUTHORIZED_CLOUD_COST_USD:
        report["status"] = "failed_budget_guard"
    elif all(call.status == "complete" for call in result.call_records):
        report["status"] = "passed"
    else:
        report["status"] = "partial"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_joint_smoke(database=args.database, output=args.output))
    except BaseException as exc:
        print(f"R3 Joint smoke failed safely: {type(exc).__name__}")
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
