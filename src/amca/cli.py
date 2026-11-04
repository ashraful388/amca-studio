"""Command-line entry point for the first AMCA vertical slice."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from amca import __version__
from amca.benchmarks import (
    load_benchmark_suite,
    load_response_artifacts,
    require_evaluator,
    run_benchmark_suite,
)
from amca.domain import (
    CollaborationMode,
    ComputePolicy,
    GoalContract,
    GoalCriterion,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from amca.credentials import (
    CredentialNotFound,
    ProviderCredentialResolver,
    WindowsCredentialStore,
)
from amca.exporting import (
    ExportProjectNotFoundError,
    audit_log_view,
    build_audit_export,
    write_json_document,
)
from amca.hardware import profile_local_compute
from amca.kernel import (
    SEMANTIC_RESPONSE_INSTRUCTIONS,
    CollaborationKernel,
    KernelConfig,
    KernelResult,
)
from amca.lifecycle import (
    ProjectDefinition,
    ProjectSummary,
    create_project,
    list_projects,
    start_project,
    stop_project,
    validate_project_id,
    wait_for_approval_project,
)
from amca.models import (
    AnthropicAdapter,
    GeminiAdapter,
    KimiAdapter,
    LlamaCppAdapter,
    ModelAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    ScriptedAdapter,
    SGLangAdapter,
    VLLMAdapter,
)
from amca.local_runtime import (
    LocalRuntimeStore,
    discover_ollama_models,
    measure_ollama_model,
)
from amca.permissions import ApprovalStatus, PermissionStore
from amca.privacy import (
    DEFAULT_PRIVATE_HYBRID_SYSTEM,
    PrivateHybridAdapter,
    TransmissionStore,
    request_transmission_approval,
    transmission_destination,
)
from amca.routing import ComplexityRouter, RoutingDecision, route_project
from amca.recovery import ProjectNotFoundError, recover_project
from amca.state import StateManager
from amca.storage import SQLiteStore


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _default_benchmark_file() -> str:
    """Find fixtures in a source checkout or an installed wheel."""

    candidates = (
        Path(__file__).resolve().parents[2] / "benchmarks" / "tasks.json",
        Path(sys.prefix) / "share" / "amca" / "benchmarks" / "tasks.json",
        Path("benchmarks/tasks.json"),
    )
    selected = next(
        (candidate for candidate in candidates if candidate.is_file()),
        candidates[-1],
    )
    return str(selected)


def _validated_project_id(value: str) -> str:
    """Return a project id that is also safe as a default path component."""

    return validate_project_id(value)


def _routing_dict(decision: RoutingDecision) -> dict[str, object]:
    return {
        "complexity": decision.complexity.value,
        "collaboration_mode": decision.collaboration_mode.value,
        "sync_mode": decision.sync_mode.value,
        "selected_adapter_ids": list(decision.selected_adapter_ids),
        "reasons": list(decision.reasons),
    }


def _write_result(
    result: KernelResult,
    output: Path | None,
    *,
    routing: RoutingDecision | None = None,
) -> None:
    document = result.to_dict()
    if routing is not None:
        document["routing"] = _routing_dict(routing)
    if output is not None:
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_json(document) + "\n", encoding="utf-8")
        document["result_file"] = str(output)
    print(_json(document))


def _script(
    summary: str,
    *,
    events: list[dict[str, object]] | None = None,
    criteria: Sequence[GoalCriterion] = (),
    focus_resolved: bool = False,
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "events": events or [],
            "criterion_evidence": [
                {
                    "criterion_id": criterion.id,
                    "evidence": f"Offline demonstration: {summary}",
                }
                for criterion in criteria
            ],
            "focus_resolved": focus_resolved,
        }
    )


def _offline_demo_adapters(criteria: Sequence[GoalCriterion]) -> tuple[ModelAdapter, ...]:
    """Build three transparent scripted perspectives; these are not API models."""

    first_round = (
        _script(
            "Proposed an induction invariant and isolated the boundary condition.",
            events=[
                {
                    "type": "hypothesis",
                    "content": "Use an invariant that is preserved by each update.",
                    "severity": "info",
                    "confidence": 0.72,
                    "evidence_refs": [],
                }
            ],
        ),
        _script(
            "Challenged the proposal because its boundary case was not yet checked.",
            events=[
                {
                    "type": "objection",
                    "id": "demo-boundary-objection",
                    "content": "The invariant needs an explicit boundary-case check.",
                    "severity": "high",
                    "confidence": 0.91,
                    "evidence_refs": [],
                    "disputed_claim": "The invariant is sufficient as stated.",
                    "proposed_check": "Substitute the smallest allowed value.",
                }
            ],
        ),
        _script(
            "Requested a discriminating boundary calculation for the shared focus.",
            events=[
                {
                    "type": "tool_request",
                    "content": "Evaluate the recurrence's normalized boundary expression.",
                    "severity": "info",
                    "confidence": 1.0,
                    "evidence_refs": [],
                    "tool": "math",
                    "operation": "evaluate",
                    "expression": "0 ** 2 + 1",
                    "disagreement_id": "demo-boundary-objection",
                }
            ],
        ),
    )
    second_round = (
        _script(
            "Refined the invariant using the new boundary evidence.",
            events=[
                {
                    "type": "correction",
                    "content": "State the invariant with its boundary domain explicitly.",
                    "severity": "info",
                    "confidence": 0.94,
                    "evidence_refs": ["tool:demo-boundary-objection"],
                }
            ],
            criteria=criteria,
            focus_resolved=True,
        ),
        _script(
            "Withdrew the objection after the discriminating calculation passed.",
            events=[
                {
                    "type": "consensus",
                    "content": "The boundary objection is resolved by direct substitution.",
                    "severity": "info",
                    "confidence": 0.98,
                    "evidence_refs": ["tool:demo-boundary-objection"],
                    "disagreement_id": "demo-boundary-objection",
                    "resolution": "Boundary case verified; retain the corrected invariant.",
                    "accepted_fact": "The corrected invariant includes the boundary case.",
                }
            ],
            criteria=criteria,
            focus_resolved=True,
        ),
        _script(
            "Independently reviewed the corrected conclusion against the shared evidence.",
            events=[
                {
                    "type": "evidence",
                    "content": "The corrected invariant and boundary calculation are consistent.",
                    "severity": "info",
                    "confidence": 0.96,
                    "evidence_refs": ["corrected invariant", "boundary substitution"],
                }
            ],
            criteria=criteria,
            focus_resolved=True,
        ),
    )
    names = ("scripted-gpt-perspective", "scripted-claude-perspective", "scripted-kimi-perspective")
    delays = (0.015, 0.03, 0.02)
    return tuple(
        ScriptedAdapter(
            [first_round[index], second_round[index]],
            adapter_id=name,
            model=name,
            provider="offline-script",
            delay_seconds=delays[index],
            is_local=True,
        )
        for index, name in enumerate(names)
    )


def _new_project(
    *,
    project_id: str,
    goal: str,
    privacy_mode: PrivacyMode,
    compute_policy: ComputePolicy,
) -> ProjectState:
    project_id = _validated_project_id(project_id)
    criteria = [
        GoalCriterion(description="A synchronized evidence-backed conclusion is recorded."),
        GoalCriterion(description="The active focus has no unresolved critical objection."),
    ]
    return ProjectState(
        project_id=project_id,
        goal=goal,
        goal_contract=GoalContract(
            goal=goal,
            criteria=criteria,
            max_iterations=6,
        ),
        current_focus=goal,
        active_subproblem=goal,
        privacy_mode=privacy_mode,
        compute_policy=compute_policy,
    )


def _demo_criterion_evaluator(
    state: ProjectState,
    criterion: GoalCriterion,
    attestations: Mapping[str, str],
) -> str | None:
    """Verify the scripted demo's two declared criteria from local state."""

    # Solo demonstrations have one participant; collaborative demonstrations
    # require corroboration from at least two independent participants.
    required_attestations = (
        1
        if state.current_collaboration_mode is CollaborationMode.SOLO
        else 2
    )
    if len(attestations) < required_attestations:
        return None
    if "no unresolved critical objection" in criterion.description.casefold():
        if not state.unresolved_disagreements:
            return "Reducer check: no unresolved disagreement remains."
        return None
    has_tool_evidence = any(
        isinstance(result, dict) and result.get("ok") is True
        for result in state.experimental_results
    )
    if has_tool_evidence and state.accepted_facts and not state.has_critical_objection:
        return (
            "Deterministic demo evaluator: local math evidence, a recorded accepted "
            "fact, and synchronized independent attestations are present."
        )
    return None


async def _demo(args: argparse.Namespace) -> int:
    project_id = _validated_project_id(
        args.project_id or f"demo-{uuid4().hex[:10]}"
    )
    state = _new_project(
        project_id=project_id,
        goal=args.goal,
        privacy_mode=PrivacyMode.OFFLINE,
        compute_policy=ComputePolicy.RESEARCH,
    )
    database = Path(args.database)
    output = Path(args.output) if args.output else database.parent / project_id / "result.json"
    with SQLiteStore(database) as store:
        adapter_catalog = _offline_demo_adapters(state.goal_contract.criteria)
        routing = route_project(state, adapter_catalog)
        selected_ids = set(routing.selected_adapter_ids)
        selected = tuple(
            adapter for adapter in adapter_catalog if adapter.adapter_id in selected_ids
        )
        manager = StateManager(state, store=store)
        kernel = CollaborationKernel(
            manager,
            selected,
            criterion_evaluator=_demo_criterion_evaluator,
            config=KernelConfig(max_rounds=2, max_staleness_versions=8),
        )
        result = await kernel.run(
            mode=routing.collaboration_mode,
            sync_mode=routing.sync_mode,
        )
    _write_result(result, output, routing=routing)
    return 0 if result.state.goal_satisfied else 2


def _load_pricing(path: str | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    models = value.get("models") if isinstance(value, dict) else None
    if not isinstance(models, dict):
        raise ValueError("pricing file requires a 'models' object")
    return {str(key): dict(item) for key, item in models.items() if isinstance(item, dict)}


def _prices(
    model: str,
    pricing: dict[str, dict[str, Any]],
    *,
    local: bool,
) -> tuple[float, float]:
    entry = pricing.get(model)
    if entry is None:
        if local:
            return 0.0, 0.0
        raise ValueError(
            f"No pricing entry for cloud model {model!r}. Supply a current --pricing "
            "file; unpriced paid calls are disabled in this milestone."
        )
    prices = (
        float(entry.get("input_per_million_tokens", 0.0)),
        float(entry.get("output_per_million_tokens", 0.0)),
    )
    if not all(math.isfinite(price) and price >= 0 for price in prices):
        raise ValueError(f"Pricing for {model!r} must contain finite non-negative rates")
    return prices


_LOCAL_PROVIDER_KINDS = {"ollama", "llamacpp", "vllm", "sglang"}


def _provider_credential_resolver() -> ProviderCredentialResolver:
    store = WindowsCredentialStore() if WindowsCredentialStore.is_available() else None
    return ProviderCredentialResolver(store)


def _live_adapters(args: argparse.Namespace) -> tuple[ModelAdapter, ...]:
    pricing = _load_pricing(args.pricing)
    credential_resolver = _provider_credential_resolver()
    adapters: list[ModelAdapter] = []
    for specification in args.provider:
        if "=" not in specification:
            raise ValueError("provider must use KIND=MODEL, for example ollama=qwen3")
        kind, model = (part.strip() for part in specification.split("=", 1))
        if not kind or not model:
            raise ValueError("provider kind and model cannot be empty")
        kind = kind.casefold()
        local = kind in _LOCAL_PROVIDER_KINDS
        input_price, output_price = _prices(
            model, pricing, local=local
        )
        common = {
            "model": model,
            "input_cost_per_million": input_price,
            "output_cost_per_million": output_price,
        }
        if kind == "openai":
            adapters.append(OpenAIAdapter(**common, credential_resolver=credential_resolver))
        elif kind == "anthropic":
            adapters.append(AnthropicAdapter(**common, credential_resolver=credential_resolver))
        elif kind == "kimi":
            adapters.append(KimiAdapter(**common, credential_resolver=credential_resolver))
        elif kind == "gemini":
            adapters.append(GeminiAdapter(**common, credential_resolver=credential_resolver))
        elif kind == "ollama":
            # Ollama accounts local time rather than an API token price.
            adapters.append(
                OllamaAdapter(model=model, base_url=args.ollama_url)
            )
        elif kind == "llamacpp":
            adapters.append(LlamaCppAdapter(**common, base_url=args.llama_cpp_url))
        elif kind == "vllm":
            adapters.append(VLLMAdapter(**common, base_url=args.vllm_url))
        elif kind == "sglang":
            adapters.append(SGLangAdapter(**common, base_url=args.sglang_url))
        else:
            raise ValueError(f"Unsupported provider kind: {kind}")
    return tuple(adapters)


def _bind_run_project(
    database: Path,
    proposed: ProjectState,
    *,
    activate: bool,
) -> ProjectState:
    """Create a durable run target or reuse its exact fresh Goal Contract.

    Reusing ``--project-id`` must never replace version zero.  A pending event
    also blocks new provider work until the explicit recovery command has
    reconciled it.
    """

    with SQLiteStore(database) as store:
        state = store.load_state(proposed.project_id)
        if state is None:
            state = proposed.copy()
            state.status = "created"
            store.save_state(state)
        else:
            if state.goal_contract is None:
                raise ValueError(
                    f"Project {state.project_id!r} has no Goal Contract and cannot run"
                )
            if state.goal != proposed.goal:
                raise ValueError(
                    f"--goal conflicts with persisted project {state.project_id!r}"
                )
            if state.privacy_mode is not proposed.privacy_mode:
                raise ValueError(
                    f"--privacy conflicts with persisted project {state.project_id!r}"
                )
            if state.compute_policy is not proposed.compute_policy:
                raise ValueError(
                    "--compute-policy conflicts with persisted project "
                    f"{state.project_id!r}"
                )
            requested_api_budget = proposed.goal_contract.max_api_cost_usd
            persisted_api_budget = state.goal_contract.max_api_cost_usd
            if (
                requested_api_budget is not None
                and persisted_api_budget != requested_api_budget
            ):
                raise ValueError(
                    "--max-api-cost conflicts with the persisted Goal Contract"
                )
        pending = store.pending_events(state.project_id)
        if pending:
            raise ValueError(
                f"Project {state.project_id!r} has {len(pending)} pending event(s); "
                "run `amca project resume --project-id ...` before starting providers"
            )
        if activate:
            start_project(
                store,
                state.project_id,
                reason="Provider execution started by the user.",
            )
            state = store.load_state(state.project_id)
            if state is None:  # pragma: no cover - guarded by the same transaction path.
                raise RuntimeError("project disappeared after lifecycle transition")
        else:
            wait_for_approval_project(store, state.project_id)
            state = store.load_state(state.project_id)
            if state is None:  # pragma: no cover - guarded by the same transaction path.
                raise RuntimeError("project disappeared after lifecycle transition")
        return state


async def _run_live(args: argparse.Namespace) -> int:
    if args.max_api_cost is not None and (
        not math.isfinite(args.max_api_cost) or args.max_api_cost < 0
    ):
        raise ValueError("--max-api-cost must be finite and non-negative")
    privacy = PrivacyMode(args.privacy)
    provider_kinds = {
        specification.split("=", 1)[0].strip()
        for specification in args.provider
        if "=" in specification
    }
    has_cloud_provider = any(
        kind.casefold() not in _LOCAL_PROVIDER_KINDS for kind in provider_kinds
    )
    if privacy is PrivacyMode.OFFLINE and has_cloud_provider:
        raise ValueError("Offline mode cannot be combined with cloud providers")
    if args.max_api_cost is None and has_cloud_provider:
        raise ValueError("Cloud execution requires an explicit --max-api-cost budget")
    project_id = _validated_project_id(
        args.project_id or f"task-{uuid4().hex[:10]}"
    )
    if privacy is PrivacyMode.PRIVATE_HYBRID and has_cloud_provider and not args.project_id:
        raise ValueError(
            "Private Hybrid cloud execution requires --project-id so an exact approval "
            "can be reviewed and reused without changing scope"
        )
    private_excerpt: str | None = None
    private_system = SEMANTIC_RESPONSE_INSTRUCTIONS + "\n\n" + DEFAULT_PRIVATE_HYBRID_SYSTEM
    if privacy is PrivacyMode.PRIVATE_HYBRID and has_cloud_provider:
        if not args.private_excerpt:
            raise ValueError(
                "Private Hybrid cloud execution requires --private-excerpt pointing to "
                "the exact UTF-8 text the user reviewed"
            )
        private_excerpt = Path(args.private_excerpt).read_text(encoding="utf-8")
    adapters = _live_adapters(args)
    state = _new_project(
        project_id=project_id,
        goal=args.goal,
        privacy_mode=privacy,
        compute_policy=ComputePolicy(args.compute_policy),
    )
    state.goal_contract.max_api_cost_usd = args.max_api_cost
    database = Path(args.database)
    state = _bind_run_project(
        database,
        state,
        activate=private_excerpt is None or bool(args.approval_id),
    )

    if private_excerpt is not None and not args.approval_id:
        # Phase one is deliberately network-free: durably create the project and
        # one independently reviewable exact-scope request per cloud endpoint.
        requests = []
        with PermissionStore(database) as permissions:
            for adapter in adapters:
                if adapter.capabilities().is_local:
                    continue
                request = request_transmission_approval(
                    permissions,
                    project_id=project_id,
                    adapter_id=adapter.adapter_id,
                    provider=adapter.provider,
                    model=adapter.model,
                    excerpt=private_excerpt,
                    system_prompt=private_system,
                    max_output_tokens=args.hybrid_output_cap,
                    destination=transmission_destination(adapter),
                    max_uses=args.hybrid_max_transmissions,
                    ttl_seconds=args.hybrid_approval_ttl,
                )
                requests.append(request.to_dict())
        print(
            _json(
                {
                    "status": "waiting_for_approval",
                    "project_id": project_id,
                    "network_started": False,
                    "private_excerpt_file": str(Path(args.private_excerpt).resolve()),
                    "approval_requests": requests,
                    "next": (
                        "Approve each id with `amca approvals approve --id ID`, then rerun "
                        "the same command with each `--approval-id ID`."
                    ),
                }
            )
        )
        return 3

    if private_excerpt is not None:
        guarded: list[ModelAdapter] = []
        for adapter in adapters:
            if adapter.capabilities().is_local:
                guarded.append(adapter)
                continue
            guarded.append(
                PrivateHybridAdapter(
                    adapter,
                    project_id=project_id,
                    database=database,
                    excerpt=private_excerpt,
                    approval_ids=args.approval_id,
                    system_prompt=private_system,
                    max_output_tokens=args.hybrid_output_cap,
                )
            )
        adapters = tuple(guarded)

    profile = profile_local_compute(database.parent)
    with LocalRuntimeStore(database) as runtime_store:
        runtime_store.save_hardware_profile(profile)
        local_runtime = runtime_store.snapshot()
    routing: RoutingDecision | None = None
    if args.mode == "auto":
        routing = route_project(
            state,
            adapters,
            novelty_required=args.novelty,
            local_runtime=local_runtime,
        )
        selected_ids = set(routing.selected_adapter_ids)
        adapters = tuple(adapter for adapter in adapters if adapter.adapter_id in selected_ids)
        mode = routing.collaboration_mode
        if mode not in {CollaborationMode.SOLO, CollaborationMode.JOINT}:
            mode = (
                CollaborationMode.JOINT
                if len(adapters) >= 2
                else CollaborationMode.SOLO
            )
            fallback_sync = (
                SyncMode.BOUNDED_STALENESS
                if mode is CollaborationMode.JOINT
                else SyncMode.SOFT
            )
            routing = RoutingDecision(
                complexity=routing.complexity,
                collaboration_mode=mode,
                sync_mode=fallback_sync,
                selected_adapter_ids=routing.selected_adapter_ids,
                reasons=routing.reasons
                + (
                    "The preferred mode is specified but not executable in the first "
                    f"vertical slice; safely fell back to {mode.value}.",
                ),
            )
        sync_mode = routing.sync_mode if args.sync == "auto" else SyncMode(args.sync)
    else:
        state.complexity = ComplexityRouter().assess(
            state.goal, novelty_required=args.novelty
        )
        mode = CollaborationMode(args.mode)
        sync_mode = SyncMode.HARD if args.sync == "auto" else SyncMode(args.sync)
    if mode is CollaborationMode.JOINT and len(adapters) < 2:
        raise ValueError("Joint mode requires at least two eligible --provider entries")
    output = Path(args.output) if args.output else database.parent / project_id / "result.json"
    with SQLiteStore(database) as store:
        result = await CollaborationKernel(
            StateManager(state, store=store),
            adapters,
            config=KernelConfig(max_rounds=args.rounds),
        ).run(mode=mode, sync_mode=sync_mode)
    _write_result(result, output, routing=routing)
    return 0 if result.state.goal_satisfied else 2


def _project_definition(args: argparse.Namespace) -> ProjectDefinition:
    """Build a fresh project definition from a strict JSON file and CLI fields."""

    document: dict[str, Any] = {}
    if args.contract:
        raw = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("contract file must contain one JSON object")
        document = dict(raw)

    def merge_scalar(field: str, value: Any, *, aliases: Sequence[str] = ()) -> None:
        if value is None:
            return
        for name in (field, *aliases):
            if name in document and document[name] != value:
                raise ValueError(
                    f"--{field.replace('_', '-')} conflicts with contract field {name}"
                )
        document[field] = value

    merge_scalar("project_id", args.project_id)
    merge_scalar("goal", args.goal, aliases=("objective",))
    merge_scalar("privacy_mode", args.privacy)
    merge_scalar("compute_policy", args.compute_policy)

    added_criteria: list[dict[str, Any]] = [
        {"description": description} for description in args.criterion
    ]
    for index, raw_criterion in enumerate(args.criterion_json):
        value = json.loads(raw_criterion)
        if not isinstance(value, dict):
            raise ValueError(f"--criterion-json entry {index + 1} must be an object")
        added_criteria.append(value)
    if added_criteria:
        if (
            "criteria" in document
            and "success_criteria" in document
            and document["criteria"] != document["success_criteria"]
        ):
            raise ValueError("contract criteria conflicts with success_criteria")
        existing_criteria = document.get(
            "criteria", document.get("success_criteria", [])
        )
        if not isinstance(existing_criteria, list):
            raise ValueError("contract criteria must be a JSON array")
        document.pop("success_criteria", None)
        document["criteria"] = list(existing_criteria) + added_criteria

    if args.constraint:
        if (
            "constraints" in document
            and "user_constraints" in document
            and document["constraints"] != document["user_constraints"]
        ):
            raise ValueError("contract constraints conflicts with user_constraints")
        existing_constraints = document.get(
            "constraints", document.get("user_constraints", [])
        )
        if not isinstance(existing_constraints, list):
            raise ValueError("contract constraints must be a JSON array")
        document.pop("user_constraints", None)
        document["constraints"] = list(existing_constraints) + list(args.constraint)

    budget = document.get("budget", {})
    if budget is None:
        budget = {}
    if not isinstance(budget, dict):
        raise ValueError("contract budget must be a JSON object")
    budget = dict(budget)
    budget_arguments = {
        "max_iterations": args.max_iterations,
        "max_runtime_seconds": args.max_runtime_seconds,
        "max_api_cost_usd": args.max_api_cost,
    }
    for field, value in budget_arguments.items():
        if value is None:
            continue
        if field in budget and budget[field] != value:
            raise ValueError(
                f"--{field.replace('_', '-')} conflicts with contract budget.{field}"
            )
        if field in document and document[field] != value:
            raise ValueError(
                f"--{field.replace('_', '-')} conflicts with contract field {field}"
            )
        budget[field] = value
    if (
        "max_iterations" not in budget
        and "iteration_cap" not in budget
        and "max_iterations" not in document
    ):
        # A new CLI project is bounded by default even when the user only
        # supplies an objective and criteria.
        budget["max_iterations"] = 6
    document["budget"] = budget

    return ProjectDefinition.from_mapping(document)


def _project_create(args: argparse.Namespace) -> int:
    definition = _project_definition(args)
    with SQLiteStore(args.database) as store:
        state = create_project(store, definition)
    print(
        _json(
            {
                "created": True,
                "project": ProjectSummary.from_state(state).to_dict(),
                "goal_contract": state.goal_contract.to_dict(),
                "constraints": list(state.user_constraints),
            }
        )
    )
    return 0


def _project_list(args: argparse.Namespace) -> int:
    with SQLiteStore(args.database) as store:
        projects = [
            summary.to_dict()
            for summary in list_projects(store, status=args.status)
        ]
    print(_json({"count": len(projects), "projects": projects}))
    return 0


def _project_show(args: argparse.Namespace) -> int:
    with SQLiteStore(args.database) as store:
        state = store.load_state(_validated_project_id(args.project_id), args.version)
    if state is None:
        print(f"Project {args.project_id!r} was not found.", file=sys.stderr)
        return 1
    print(state.to_json(indent=2))
    return 0


def _project_stop(args: argparse.Namespace) -> int:
    with SQLiteStore(args.database) as store:
        transition = stop_project(store, args.project_id, reason=args.reason)
    print(_json(transition.to_dict()))
    return 0


def _profile(args: argparse.Namespace) -> int:
    profile = profile_local_compute(args.path)
    with LocalRuntimeStore(args.database) as store:
        profile_sha256 = store.save_hardware_profile(profile)
    document = profile.to_dict()
    document["profile_sha256"] = profile_sha256
    document["persisted_database"] = str(Path(args.database).resolve())
    print(_json(document))
    return 0


def _runtime_snapshot_document(store: LocalRuntimeStore) -> dict[str, Any]:
    snapshot = store.snapshot()
    return {
        "hardware_profile_sha256": snapshot.hardware_profile_sha256,
        "hardware_profile": (
            snapshot.hardware_profile.to_dict() if snapshot.hardware_profile else None
        ),
        "inventory_providers": list(snapshot.inventory_providers),
        "models": [model.to_dict() for model in snapshot.models],
        "measurements": [measurement.to_dict() for measurement in snapshot.measurements],
    }


def _local_models(args: argparse.Namespace) -> int:
    database = Path(args.database)
    with LocalRuntimeStore(database) as store:
        if args.action == "refresh":
            profile = profile_local_compute(database.parent)
            store.save_hardware_profile(profile)
            models = discover_ollama_models(
                args.ollama_url,
                timeout=args.timeout,
            )
            store.replace_inventory("ollama", models)
        elif args.action == "measure":
            if not args.model:
                raise ValueError("local-models measure requires --model")
            profile = profile_local_compute(database.parent)
            store.save_hardware_profile(profile)
            snapshot = store.snapshot()
            if "ollama" not in snapshot.inventory_providers:
                raise ValueError("refresh the Ollama inventory before measuring a model")
            if snapshot.model("ollama", args.model) is None:
                raise ValueError(f"Ollama model {args.model!r} is not in the current inventory")
            measurement = measure_ollama_model(
                args.model,
                profile,
                base_url=args.ollama_url,
                timeout=args.timeout,
            )
            store.record_measurement(measurement)
        document = _runtime_snapshot_document(store)
    document["database"] = str(database.resolve())
    print(_json(document))
    if args.action == "measure":
        return 0 if document["measurements"][0]["success"] else 2
    return 0


def _approvals(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if args.action == "list":
        with PermissionStore(database) as store:
            requests = store.list(
                project_id=args.project_id,
                status=args.status,
            )
        print(_json({"approvals": [request.to_dict() for request in requests]}))
        return 0
    if args.action in {"approve", "deny"}:
        if not args.id:
            raise ValueError(f"approvals {args.action} requires --id")
        with PermissionStore(database) as store:
            request = store.decide(
                args.id,
                approve=args.action == "approve",
                decided_by="cli-user",
                reason=args.reason,
            )
        print(_json(request.to_dict()))
        return 0
    with TransmissionStore(database) as store:
        transmissions = store.list(project_id=args.project_id)
    print(
        _json(
            {
                "transmissions": [record.to_dict() for record in transmissions],
                "content_persisted": False,
            }
        )
    )
    return 0


def _credentials(args: argparse.Namespace) -> int:
    """Manage provider keys without accepting or displaying secret values."""

    if not WindowsCredentialStore.is_available():
        raise ValueError("persistent credentials require Windows Credential Manager")
    store = WindowsCredentialStore()
    providers = (
        [args.provider]
        if args.provider
        else ["openai", "anthropic", "kimi", "gemini"]
    )
    if args.action == "status":
        status: dict[str, bool] = {}
        for provider in providers:
            try:
                store.get(provider)
            except CredentialNotFound:
                status[provider] = False
            else:
                status[provider] = True
        print(_json({"persistent_store": "windows_credential_manager", "configured": status}))
        return 0
    if not args.provider:
        raise ValueError(f"credentials {args.action} requires --provider")
    if args.action == "set":
        secret = getpass.getpass(f"Enter the {args.provider} API key (input hidden): ")
        store.set(args.provider, secret)
        print(
            _json(
                {
                    "provider": args.provider,
                    "configured": True,
                    "persistent_store": "windows_credential_manager",
                    "secret_displayed": False,
                }
            )
        )
        return 0
    deleted = store.delete(args.provider)
    print(_json({"provider": args.provider, "deleted": deleted}))
    return 0


def _inspect(args: argparse.Namespace) -> int:
    with SQLiteStore(args.database) as store:
        if args.project_id is None:
            print(_json({"projects": store.list_projects()}))
            return 0
        state = store.load_state(args.project_id, args.version)
    if state is None:
        print(f"Project {args.project_id!r} was not found.", file=sys.stderr)
        return 1
    print(state.to_json(indent=2))
    return 0


def _resume(args: argparse.Namespace) -> int:
    try:
        with SQLiteStore(args.database) as store:
            report = recover_project(
                store,
                args.project_id,
                dry_run=args.dry_run,
            )
    except ProjectNotFoundError:
        print(f"Project {args.project_id!r} was not found.", file=sys.stderr)
        return 1
    document = report.to_dict()
    if args.output:
        output = Path(args.output).expanduser().resolve()
        document["recovery_report_file"] = str(output)
        write_json_document(document, output, overwrite=args.force)
    print(_json(document))
    return 0 if report.completed else 2


def _export_project(args: argparse.Namespace, *, audit_only: bool) -> int:
    try:
        with SQLiteStore(args.database) as store:
            document = build_audit_export(
                store,
                args.project_id,
                include_snapshots=not audit_only,
            )
    except ExportProjectNotFoundError:
        print(f"Project {args.project_id!r} was not found.", file=sys.stderr)
        return 1
    if audit_only:
        document = audit_log_view(document)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        document["export_file"] = str(output)
        write_json_document(document, output, overwrite=args.force)
    print(_json(document))
    audit = document["audit"]
    return 0 if (
        audit["unknown_event_count"] == 0
        and audit["event_digest_mismatch_count"] == 0
    ) else 2


async def _run_benchmarks(args: argparse.Namespace) -> int:
    suite = load_benchmark_suite(args.file)
    evaluator = require_evaluator(args.evaluator)
    responses = (
        load_response_artifacts(
            args.responses,
            valid_task_ids=[task.task_id for task in suite.tasks],
        )
        if args.responses
        else None
    )
    conditions = (
        (CollaborationMode.SOLO, CollaborationMode.JOINT)
        if args.condition == "both"
        else (CollaborationMode(args.condition),)
    )
    with SQLiteStore(args.database) as store:
        manifest = await run_benchmark_suite(
            suite,
            store,
            conditions=conditions,
            task_ids=args.task,
            responses=responses,
            evaluator=evaluator,
            run_id=args.run_id,
        )
    if args.output:
        output = Path(args.output).expanduser().resolve()
        write_json_document(manifest, output, overwrite=args.force)
    print(_json(manifest))
    return 0


def _benchmarks(args: argparse.Namespace) -> int:
    suite = load_benchmark_suite(args.file)
    if args.action == "run":
        return asyncio.run(_run_benchmarks(args))
    rows = [
        {
            "id": task.task_id,
            "domain": task.domain,
            "title": task.title,
        }
        for task in suite.tasks
    ]
    print(
        _json(
            {
                "suite_id": suite.suite_id,
                "schema_version": suite.schema_version,
                "count": len(rows),
                "tasks": rows,
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amca",
        description="AMCA Phase 0 + first Python collaboration-kernel slice",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    project = subparsers.add_parser(
        "project",
        help="create, list, inspect, stop, or recover durable local projects",
    )
    project_actions = project.add_subparsers(dest="project_action", required=True)

    project_create = project_actions.add_parser(
        "create",
        help="create a fresh versioned project with an explicit Goal Contract",
    )
    project_create.add_argument(
        "--contract",
        help="strict JSON project/Goal Contract definition",
    )
    project_create.add_argument(
        "--project-id",
        help="safe local identity; may instead be supplied by --contract",
    )
    project_create.add_argument(
        "--goal",
        help="objective; may instead be supplied as goal/objective by --contract",
    )
    project_create.add_argument(
        "--criterion",
        action="append",
        default=[],
        help="required custom criterion description; repeat for more",
    )
    project_create.add_argument(
        "--criterion-json",
        action="append",
        default=[],
        help=(
            "fresh criterion JSON with id, description, evaluator_kind, expected_value, "
            "tolerance, evidence_requirement, and decision_class"
        ),
    )
    project_create.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="durable user constraint; repeat for more",
    )
    project_create.add_argument("--max-iterations", type=int)
    project_create.add_argument("--max-runtime-seconds", type=float)
    project_create.add_argument("--max-api-cost", type=float)
    project_create.add_argument(
        "--privacy",
        choices=("offline", "private_hybrid", "full_frontier"),
    )
    project_create.add_argument(
        "--compute-policy",
        choices=("economy", "balanced", "research", "maximum_intelligence"),
    )
    project_create.add_argument("--database", default=".amca/amca.sqlite3")
    project_create.set_defaults(handler=_project_create)

    project_list = project_actions.add_parser(
        "list", help="list safe project summaries without transcript content"
    )
    project_list.add_argument("--database", default=".amca/amca.sqlite3")
    project_list.add_argument(
        "--status",
        choices=(
            "created",
            "active",
            "degraded",
            "blocked",
            "waiting_for_approval",
            "complete",
            "failed",
            "stopped",
            "budget_exhausted",
        ),
    )
    project_list.set_defaults(handler=_project_list)

    project_show = project_actions.add_parser(
        "show", help="print the latest or an immutable project snapshot"
    )
    project_show.add_argument("--database", default=".amca/amca.sqlite3")
    project_show.add_argument("--project-id", required=True)
    project_show.add_argument("--version", type=int)
    project_show.set_defaults(handler=_project_show)

    project_stop = project_actions.add_parser(
        "stop", help="durably stop a project without claiming goal success"
    )
    project_stop.add_argument("--database", default=".amca/amca.sqlite3")
    project_stop.add_argument("--project-id", required=True)
    project_stop.add_argument("--reason", default="Stopped by the user.")
    project_stop.set_defaults(handler=_project_stop)

    project_resume = project_actions.add_parser(
        "resume",
        help="recover durable pending events without restarting external work",
    )
    project_resume.add_argument("--database", default=".amca/amca.sqlite3")
    project_resume.add_argument("--project-id", required=True)
    project_resume.add_argument("--dry-run", action="store_true")
    project_resume.add_argument("--output")
    project_resume.add_argument("--force", action="store_true")
    project_resume.set_defaults(handler=_resume)

    demo = subparsers.add_parser("demo", help="run a fully offline two-round Joint Deliberation")
    demo.add_argument(
        "--goal",
        default=(
            "Derive and prove an invariant for a complex scientific recurrence, then "
            "verify that it remains valid at the smallest allowed boundary value."
        ),
    )
    demo.add_argument("--project-id")
    demo.add_argument("--database", default=".amca/amca.sqlite3")
    demo.add_argument("--output")
    demo.set_defaults(handler=lambda args: asyncio.run(_demo(args)))

    live = subparsers.add_parser("run", help="run configured API/local providers")
    live.add_argument("--goal", required=True)
    live.add_argument(
        "--provider",
        action="append",
        required=True,
        help=(
            "repeat KIND=MODEL; KIND is openai, anthropic, kimi, gemini, "
            "ollama, llamacpp, vllm, or sglang"
        ),
    )
    live.add_argument("--mode", choices=("auto", "solo", "joint"), default="auto")
    live.add_argument(
        "--sync",
        choices=("auto", "async", "soft", "quorum", "hard", "bounded_staleness"),
        default="auto",
    )
    live.add_argument(
        "--privacy",
        choices=("offline", "private_hybrid", "full_frontier"),
        default="offline",
    )
    live.add_argument(
        "--compute-policy",
        choices=("economy", "balanced", "research", "maximum_intelligence"),
        default="research",
    )
    live.add_argument("--rounds", type=int, default=3)
    live.add_argument("--novelty", action="store_true")
    live.add_argument("--max-api-cost", type=float)
    live.add_argument("--pricing")
    live.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    live.add_argument("--llama-cpp-url", default="http://127.0.0.1:8080/v1")
    live.add_argument("--vllm-url", default="http://127.0.0.1:8000/v1")
    live.add_argument("--sglang-url", default="http://127.0.0.1:30000/v1")
    live.add_argument(
        "--private-excerpt",
        help=(
            "UTF-8 file containing the complete and only project excerpt that a "
            "Private Hybrid cloud provider may receive"
        ),
    )
    live.add_argument(
        "--approval-id",
        action="append",
        default=[],
        help="approved exact-scope transmission id; repeat for multiple cloud providers",
    )
    live.add_argument("--hybrid-output-cap", type=int, default=1800)
    live.add_argument("--hybrid-max-transmissions", type=int, default=1)
    live.add_argument("--hybrid-approval-ttl", type=float, default=900)
    live.add_argument("--project-id")
    live.add_argument("--database", default=".amca/amca.sqlite3")
    live.add_argument("--output")
    live.set_defaults(handler=lambda args: asyncio.run(_run_live(args)))

    profile = subparsers.add_parser("profile", help="inspect local compute capacity")
    profile.add_argument("--path", default=".")
    profile.add_argument("--database", default=".amca/amca.sqlite3")
    profile.set_defaults(handler=_profile)

    local_models = subparsers.add_parser(
        "local-models",
        help="list, refresh, or measure the persisted loopback Ollama inventory",
    )
    local_models.add_argument(
        "action", nargs="?", choices=("list", "refresh", "measure"), default="list"
    )
    local_models.add_argument("--model")
    local_models.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    local_models.add_argument("--timeout", type=float, default=120)
    local_models.add_argument("--database", default=".amca/amca.sqlite3")
    local_models.set_defaults(handler=_local_models)

    approvals = subparsers.add_parser(
        "approvals",
        help="review exact-scope approvals and content-free transmission audit rows",
    )
    approvals.add_argument(
        "action",
        nargs="?",
        choices=("list", "approve", "deny", "transmissions"),
        default="list",
    )
    approvals.add_argument("--id")
    approvals.add_argument("--project-id")
    approvals.add_argument(
        "--status",
        choices=tuple(status.value for status in ApprovalStatus),
    )
    approvals.add_argument("--reason")
    approvals.add_argument("--database", default=".amca/amca.sqlite3")
    approvals.set_defaults(handler=_approvals)

    credentials = subparsers.add_parser(
        "credentials",
        help="manage provider keys in Windows Credential Manager without printing them",
    )
    credentials.add_argument(
        "action", nargs="?", choices=("status", "set", "delete"), default="status"
    )
    credentials.add_argument(
        "--provider",
        help="credential name such as openai, anthropic, kimi, or gemini",
    )
    credentials.set_defaults(handler=_credentials)

    inspect = subparsers.add_parser("inspect", help="list projects or print a saved state")
    inspect.add_argument("--database", default=".amca/amca.sqlite3")
    inspect.add_argument("--project-id")
    inspect.add_argument("--version", type=int)
    inspect.set_defaults(handler=_inspect)

    resume = subparsers.add_parser(
        "resume",
        help="replay durable pending events without restarting model calls",
    )
    resume.add_argument("--database", default=".amca/amca.sqlite3")
    resume.add_argument("--project-id", required=True)
    resume.add_argument(
        "--dry-run",
        action="store_true",
        help="report pending events without changing persisted state",
    )
    resume.add_argument("--output")
    resume.add_argument("--force", action="store_true")
    resume.set_defaults(handler=_resume)

    export = subparsers.add_parser(
        "export",
        help="export current state, immutable snapshots, and an auditable event log",
    )
    export.add_argument("--database", default=".amca/amca.sqlite3")
    export.add_argument("--project-id", required=True)
    export.add_argument("--output")
    export.add_argument("--force", action="store_true")
    export.set_defaults(handler=lambda args: _export_project(args, audit_only=False))

    audit = subparsers.add_parser(
        "audit-log",
        help="print or export a hash-chained collaboration event log",
    )
    audit.add_argument("--database", default=".amca/amca.sqlite3")
    audit.add_argument("--project-id", required=True)
    audit.add_argument("--output")
    audit.add_argument("--force", action="store_true")
    audit.set_defaults(handler=lambda args: _export_project(args, audit_only=True))

    benchmarks = subparsers.add_parser(
        "benchmarks",
        help="list or run the Phase 0 benchmark fixtures offline",
    )
    benchmarks.add_argument(
        "action",
        nargs="?",
        choices=("list", "run"),
        default="list",
    )
    benchmarks.add_argument("--file", default=_default_benchmark_file())
    benchmarks.add_argument(
        "--condition",
        choices=("solo", "joint", "both"),
        default="both",
    )
    benchmarks.add_argument(
        "--task",
        action="append",
        help="run only this task id; repeat to select more than one",
    )
    benchmarks.add_argument(
        "--responses",
        help="offline JSON response artifacts keyed by task id",
    )
    benchmarks.add_argument(
        "--evaluator",
        choices=("deterministic", "external-model"),
        default="deterministic",
    )
    benchmarks.add_argument("--run-id")
    benchmarks.add_argument("--database", default=".amca/benchmarks.sqlite3")
    benchmarks.add_argument("--output")
    benchmarks.add_argument("--force", action="store_true")
    benchmarks.set_defaults(handler=_benchmarks)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("AMCA execution interrupted by the user.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"AMCA error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
