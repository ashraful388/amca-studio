"""Execute an agent plan's task graph, and govern the tools it may use.

The planner (`AgentFabricStore.plan`) produces a task graph whose nodes carry
``id``/``agent_id``/``depends_on``/``executor_class``/``status``.  Until now
nothing consumed it: it was persisted and displayed while the run went straight
to the collaboration kernel, so a "planned" role could be selected and never
actually tasked.  This module closes that gap in three parts:

1. **Scheduling** — adapt plan nodes into ``NanoScheduler`` tasks so the graph
   really executes, in dependency order, one model call per agent.
2. **Tool policy** — apply ``plan["tool_policy"]["assignments"]`` to each task's
   tool catalog.  The policy is an *intersection* ceiling: a tool must be both
   granted to the agent's role and present in the run's governed catalog.
   ``deny_by_default`` is honoured, so an agent with no assignment gets nothing.
3. **Handoff** — pass each dependency's result into the downstream agent's
   prompt, and record the handoff so it is auditable rather than implied.

Security properties held here (do not relax):

* Tool authorization is an intersection of two ceilings, never a union.
* A missing or malformed assignment denies, it never falls back to the full
  catalog.
* Executor kinds that require injected collaborators (deterministic executor,
  verification cells, manager proposals) are never selected by mapping — a
  plan's ``executor_class`` maps only to model-backed execution.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import hashlib

from .agent_runtime import (
    HANDOFF_EXCERPT_CHARS,
    HANDOFF_HEADER,
    HANDOFF_TOTAL_BUDGET,
    MAX_HANDOFF_CHARS,
    ExecutorKind,
)

#: Plan executor classes mapped to the scheduler's executor kinds.
#:
#: Every entry maps to a model-backed kind on purpose.  ``deterministic_tool``
#: needs an injected deterministic executor, ``verification_cell`` needs at
#: least two verifiers, and ``manager``/``collaboration`` construct their own
#: collaborators — selecting any of those from a plan label would either raise
#: or silently produce a task with no worker, which is the failure mode this
#: module exists to remove.
EXECUTOR_CLASS_MAP: dict[str, str] = {
    "deterministic_or_cheapest_adequate": ExecutorKind.NANO_LOCAL_MODEL,
    "deterministic_first": ExecutorKind.NANO_LOCAL_MODEL,
    "specialist": ExecutorKind.NANO_LOCAL_MODEL,
    "nano_or_model": ExecutorKind.NANO_LOCAL_MODEL,
    "selected_model_set": ExecutorKind.NANO_LOCAL_MODEL,
    "cheapest_adequate": ExecutorKind.NANO_LOCAL_MODEL,
}

#: Used when a plan names an executor class this build does not know.  A model
#: call is the safe default: it cannot grant a capability a tool would.
DEFAULT_EXECUTOR_KIND = ExecutorKind.NANO_LOCAL_MODEL

#: How much of an upstream result is handed to the next agent, and the marker
#: that introduces the block.  Both are defined in ``agent_runtime`` so the
#: live scheduler and this module cannot drift apart.
_ = (HANDOFF_HEADER, MAX_HANDOFF_CHARS, HANDOFF_TOTAL_BUDGET, HANDOFF_EXCERPT_CHARS)


def executor_kind_for(executor_class: str) -> str:
    """Map a plan executor class to a scheduler executor kind."""

    return EXECUTOR_CLASS_MAP.get(str(executor_class or "").strip(), DEFAULT_EXECUTOR_KIND)


def assignment_for(plan: Mapping[str, Any], agent_id: str) -> Mapping[str, Any] | None:
    """Return the tool-policy assignment for an agent, or ``None``.

    Malformed entries are treated as absent rather than skipped over, so a
    corrupted assignment denies instead of leaving the agent unconstrained.
    """

    policy = plan.get("tool_policy")
    if not isinstance(policy, Mapping):
        return None
    assignments = policy.get("assignments")
    if not isinstance(assignments, Sequence) or isinstance(assignments, (str, bytes)):
        return None
    wanted = str(agent_id or "")
    for entry in assignments:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("agent_id", "")) == wanted:
            return entry
    return None


def authorized_tool_names(
    plan: Mapping[str, Any],
    agent_id: str,
    catalog_names: Iterable[str],
) -> tuple[str, ...]:
    """The tools an agent may actually use: role ceiling ∩ run catalog.

    Fails closed.  An agent with no assignment, a non-list ``allowed_tools``, or
    a ``deny_by_default`` policy it is not named in receives an empty tuple.
    """

    catalog = [str(name) for name in catalog_names]
    policy = plan.get("tool_policy")
    deny_by_default = True
    if isinstance(policy, Mapping):
        deny_by_default = bool(policy.get("deny_by_default", True))

    entry = assignment_for(plan, agent_id)
    if entry is None:
        # Control agents and unlisted roles carry no governed tools.  This is
        # the deny_by_default contract, not a fallback.
        return ()

    allowed = entry.get("allowed_tools")
    if not isinstance(allowed, (list, tuple, set, frozenset)):
        return ()
    allowed_set = {str(name) for name in allowed}
    granted = tuple(name for name in catalog if name in allowed_set)
    if not granted and not deny_by_default:
        # An explicit opt-out of deny-by-default still cannot widen the run's
        # governed catalog, but it may pass the catalog through for a role that
        # declares no restriction at all.
        return tuple(catalog) if not allowed_set else ()
    return granted


#: Roles that govern a run rather than perform its work.  They are still
#: tasked — a planned role with no task is the "skipped role" defect — but they
#: must not be handed the user's goal verbatim.  A permission agent writing the
#: user's Python function is not governance, and it makes the "distinct tasks
#: and verifiable outputs" requirement false.
CONTROL_AGENT_BRIEFS: dict[str, str] = {
    "core.agent-governor": (
        "Act as the governance agent for this run. Do not perform the user's task. "
        "State the constraints that should apply to it, any risk you would flag, and "
        "whether you would authorise proceeding."
    ),
    "core.permission-agent": (
        "Act as the permission agent for this run. Do not perform the user's task. "
        "State which permissions, approvals and path grants this goal would require, "
        "and name anything that must be refused."
    ),
    "core.tool-router": (
        "Act as the tool router for this run. Do not perform the user's task. State "
        "which tools the goal needs and the grant or approval scope each one requires."
    ),
}

#: Prefix marking a governance task's focus.
GOVERNANCE_FRAME = "Governance role — assess, do not perform"


def is_control_agent(agent_id: str) -> bool:
    """Whether an agent governs the run instead of performing its work."""

    return str(agent_id or "") in CONTROL_AGENT_BRIEFS


def _agent_label(plan: Mapping[str, Any], agent_id: str) -> str:
    names = plan.get("agent_names")
    if isinstance(names, Mapping) and agent_id in names:
        return str(names[agent_id])
    return agent_id


def handoff_block(
    plan: Mapping[str, Any],
    node: Mapping[str, Any],
    results: Mapping[str, str],
) -> str:
    """Build the bounded, structured handoff a downstream agent receives.

    Matches ``agent_runtime.build_handoff_envelope``: identity, digest and a
    bounded excerpt per dependency, with the whole block capped so a wide graph
    cannot inflate the prompt without limit.
    """

    blocks: list[str] = []
    remaining = HANDOFF_TOTAL_BUDGET
    for dependency in node.get("depends_on", ()) or ():
        text = results.get(str(dependency))
        if not text:
            continue
        producer = ""
        for candidate in plan.get("task_graph", ()) or ():
            if isinstance(candidate, Mapping) and str(candidate.get("id", "")) == str(dependency):
                producer = _agent_label(plan, str(candidate.get("agent_id", "")))
                break
        if remaining <= 0:
            blocks.append(
                f"[{dependency}] {producer or dependency}: result withheld — "
                "handoff budget exhausted; read it from shared state if needed"
            )
            continue
        excerpt = text[: min(HANDOFF_EXCERPT_CHARS, remaining)]
        remaining -= len(excerpt)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        truncated = " (excerpt)" if len(excerpt) < len(text) else ""
        blocks.append(
            f"[{dependency}] {producer or dependency}\n"
            f"  result_digest: {digest}  chars: {len(text)}\n"
            f"  result{truncated}:\n{excerpt}"
        )
    if not blocks:
        return ""
    return f"{HANDOFF_HEADER}:\n\n" + "\n\n".join(blocks)


def graph_tasks(plan: Mapping[str, Any], goal: str) -> list[dict[str, Any]]:
    """Adapt a plan's task graph into ``NanoScheduler.schedule`` entries.

    Nodes keep their ids and dependencies, so execution order matches the plan.
    ``focus`` carries the goal plus the agent's own instruction, and any
    dependency results that are already available are appended as a handoff.
    """

    nodes = plan.get("task_graph")
    if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)) or not nodes:
        return []

    instructions = plan.get("agent_instructions")
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        task_id = str(node.get("id", "")).strip()
        agent_id = str(node.get("agent_id", "")).strip()
        if not task_id or not agent_id or task_id in seen:
            continue
        seen.add(task_id)
        instruction = ""
        if isinstance(instructions, Mapping):
            instruction = str(instructions.get(agent_id, "") or "")
        if is_control_agent(agent_id):
            # Governance agents assess the goal; they do not execute it.
            focus = f"{GOVERNANCE_FRAME}\n\n{CONTROL_AGENT_BRIEFS[agent_id]}\n\nGoal under review: {goal}"
        else:
            focus = f"{goal}\n\nYour role: {_agent_label(plan, agent_id)}."
            if instruction:
                focus += f"\n{instruction}"
        tasks.append(
            {
                "id": task_id,
                "agent_id": agent_id,
                "focus": focus[:8000],
                "executor_kind": executor_kind_for(str(node.get("executor_class", ""))),
                "depends_on": [str(dep) for dep in (node.get("depends_on", ()) or ())],
            }
        )
    return tasks


def apply_tool_policy(
    plan: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    catalog_names: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    """Per-task authorized tool names, keyed by task id."""

    catalog = [str(name) for name in catalog_names]
    return {
        str(task.get("id", "")): authorized_tool_names(plan, str(task.get("agent_id", "")), catalog)
        for task in tasks
    }


def handoff_records(
    plan: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    results: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Audit records for every dependency edge the graph actually exercised."""

    by_id = {str(task.get("id", "")): task for task in tasks}
    records: list[dict[str, Any]] = []
    for task in tasks:
        for dependency in task.get("depends_on", ()) or ():
            upstream = by_id.get(str(dependency))
            if upstream is None:
                continue
            delivered = str(results.get(str(dependency), ""))
            records.append(
                {
                    "from_task": str(dependency),
                    "from_agent": str(upstream.get("agent_id", "")),
                    "to_task": str(task.get("id", "")),
                    "to_agent": str(task.get("agent_id", "")),
                    "delivered": bool(delivered),
                    "chars": len(delivered),
                }
            )
    return records


def task_ids_missing_agent_binding(
    tasks: Sequence[Mapping[str, Any]],
    bound_agent_ids: Iterable[str],
) -> list[str]:
    """Task ids whose agent has no adapter bound.

    A planned role with no adapter cannot execute.  Reporting it is the honest
    alternative to scheduling a task that will fail at dispatch time.
    """

    bound = {str(agent_id) for agent_id in bound_agent_ids}
    return [
        str(task.get("id", ""))
        for task in tasks
        if str(task.get("agent_id", "")) not in bound
    ]


__all__ = [
    "DEFAULT_EXECUTOR_KIND",
    "EXECUTOR_CLASS_MAP",
    "HANDOFF_HEADER",
    "HANDOFF_EXCERPT_CHARS",
    "HANDOFF_TOTAL_BUDGET",
    "MAX_HANDOFF_CHARS",
    "apply_tool_policy",
    "assignment_for",
    "authorized_tool_names",
    "executor_kind_for",
    "graph_tasks",
    "handoff_block",
    "handoff_records",
    "task_ids_missing_agent_binding",
]
