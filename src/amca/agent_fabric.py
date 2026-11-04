"""Deterministic Agent Fabric catalog, policy, planning, and persistence.

This module deliberately separates logical agents from model calls.  Catalog and
planning operations are local, auditable, and cheap; execution remains governed
by the existing AMCA kernel, permission, model-routing, and cost layers.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _clean(value: Any, *, name: str, maximum: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{name} is required")
    if len(text) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return text


class AgentClass(str, Enum):
    NANO = "nano"
    MICRO = "micro"
    SPECIALIST = "specialist"
    SQUAD_MANAGER = "squad_manager"
    LEAD_MANAGER = "lead_manager"
    META_CONTROLLER = "meta_controller"


class AgentLifecycle(str, Enum):
    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class AgentCapabilityRecord:
    agent_id: str
    project_id: str
    name: str
    role: str
    specializations: tuple[str, ...]
    capabilities: tuple[str, ...]
    lifecycle_status: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "project_id": self.project_id,
            "name": self.name,
            "role": self.role,
            "specializations": list(self.specializations),
            "capabilities": list(self.capabilities),
            "lifecycle_status": self.lifecycle_status,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ResourceEnvelope:
    max_logical_agents: int = 64
    max_concurrent_agents: int = 8
    max_manager_depth: int = 2
    max_nano_tasks: int = 128
    max_cloud_calls: int = 16
    max_frontier_calls: int = 4
    max_api_cost_usd: float = 5.0
    max_runtime_seconds: int = 3600

    def __post_init__(self) -> None:
        integer_fields = (
            self.max_logical_agents, self.max_concurrent_agents,
            self.max_manager_depth, self.max_nano_tasks, self.max_cloud_calls,
            self.max_frontier_calls, self.max_runtime_seconds,
        )
        if any(value < 0 for value in integer_fields):
            raise ValueError("resource-envelope limits must be non-negative")
        if self.max_concurrent_agents > self.max_logical_agents:
            raise ValueError("concurrency cannot exceed logical-agent limit")
        if not math.isfinite(self.max_api_cost_usd) or self.max_api_cost_usd < 0:
            raise ValueError("API cost limit must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class AgentTemplate:
    id: str
    name: str
    description: str
    category: str
    agent_class: AgentClass
    origin: str = "built_in"
    enabled: bool = True
    preferred_model_policy: str = "auto_intelligence"
    allowed_tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    instructions: str = ""
    output_schema: str = "structured_result"
    success_criteria: str = "Objective completed with evidence appropriate to risk."
    can_spawn: bool = False
    max_children: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["agent_class"] = self.agent_class.value
        return value


CORE_AGENT_NAMES = (
    "Meta-Controller", "Goal Manager", "Task Planner", "Task Graph Generator",
    "Parallelizability Analyzer", "Agent Economy Manager", "Agent Governor",
    "Agent Router", "Model Router", "Collaboration Router", "Synchronization Router",
    "Nano Scheduler", "Nano Batch Optimizer", "Context Manager", "Memory Manager",
    "Retrieval Agent", "Evidence Manager", "Tool Router", "Permission Agent",
    "Privacy Agent", "Cost Controller", "Latency Controller", "Progress Monitor",
    "Recovery Agent", "Conflict Resolver", "Critic Agent", "Verification Agent",
    "Quality Reviewer", "Result Synthesizer", "Artifact Builder", "Audit Agent",
    "Provenance Agent", "Failure Handler", "Duplicate Task Detector",
    "Escalation Manager", "Local Compute Manager", "Provider Health Agent",
    "Background Task Manager", "Automation Manager", "Notification Agent",
    "Model Access Intelligence Agent (MAIA)",
)


SPECIALIST_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Research & Web": (
        "Web Search", "Deep Research", "Real-Time Information", "News Research",
        "Academic Search", "Literature Review", "Citation Verification", "Fact Check",
        "Source Validation", "Patent Research", "Standards Research", "Regulation Research",
        "Market Research", "Competitive Intelligence", "Company Research",
        "Technology Scout", "Trend Analysis", "Evidence Synthesis",
    ),
    "Files, Documents & Writing": (
        "File Search", "File Manager", "PDF", "Word Document", "Spreadsheet",
        "Presentation", "LaTeX", "Archive", "Document Extraction",
        "Document Comparison", "Document Conversion", "Document Metadata",
        "Knowledge Base", "General Writing", "Editing", "Proofreading",
        "Technical Writing", "Report Writing", "Proposal Writing", "Publishing",
        "Translation", "Summarization", "Content Transformation", "Presentation Content",
    ),
    "Data & Analytics": (
        "Data Analysis", "Data Cleaning", "Exploratory Data Analysis", "Statistics",
        "Spreadsheet Analysis", "SQL", "Database", "Data Visualization", "Forecasting",
        "Anomaly Detection", "KPI Analysis", "Experiment Analysis", "ETL",
        "Data Quality", "Dashboard Analysis", "Survey Analysis", "Optimization Analysis",
    ),
    "Software Engineering": (
        "Repository Analysis", "Coding", "Software Architecture", "Feature Development",
        "Debugging", "Refactoring", "Migration", "Unit Testing", "Integration Testing",
        "Quality Assurance", "Code Review", "Security Code Review", "Dependency Analysis",
        "Build Engineering", "API Engineering", "Frontend Engineering", "Backend Engineering",
        "Database Engineering", "Mobile Engineering", "Desktop Engineering",
        "Performance Engineering", "Developer Documentation", "Issue Triage",
        "Bug Reproduction", "Release Engineering",
    ),
    "Git & DevOps": (
        "Git", "GitHub", "GitLab", "Pull Request", "CI/CD", "DevOps", "Cloud",
        "Docker", "Kubernetes", "Deployment", "Monitoring", "Incident Response",
        "Log Analysis", "Environment Setup", "Infrastructure Review",
    ),
    "Browser, Computer & Multimedia": (
        "Browser", "Browser Navigation", "Web Extraction", "Form Assistance",
        "Web Testing", "Browser QA", "Download", "Desktop Control", "Application Control",
        "Screen Understanding", "Clipboard", "Office Automation", "Image Understanding",
        "Image Generation", "Image Editing", "Diagram", "OCR", "Audio Analysis",
        "Transcription", "Text to Speech", "Voice", "Video Understanding", "Video Editing",
        "Graphic Design", "UI/UX Design",
    ),
    "Business, Sales & Marketing": (
        "Business Analysis", "Market Sizing", "Sales Prospecting", "Lead Qualification",
        "Account Research", "Outreach", "Follow-up", "Sales Coach", "Pipeline Analysis",
        "Sales Forecast", "Marketing Strategy", "Campaign", "SEO", "Social Media",
        "Brand", "Marketing Analytics", "Customer Journey", "Customer Service",
        "Ticket Triage", "Customer Troubleshooting", "Knowledge Support",
        "Customer Escalation", "Customer Success", "Feedback Analysis",
    ),
    "Commerce, Finance, HR & Operations": (
        "Product Search", "Product Recommendation", "Shopper", "Merchant", "Pricing",
        "Promotion", "Inventory", "Order Tracking", "Financial Analysis", "Accounting",
        "Reconciliation", "Cash Management", "Accounts Payable", "Accounts Receivable",
        "Expense", "Budget", "Financial Forecast", "Invoice", "Fraud Analysis",
        "Investment Research", "HR Self-Service", "Recruiting", "Candidate Sourcing",
        "Resume Screening", "Interview Scheduling", "Onboarding", "Benefits", "Leave",
        "Payroll", "Training", "Workforce Planning", "Procurement", "Requisition",
        "Supplier Search", "Supplier Evaluation", "RFQ", "Bid Analysis", "Spend Analysis",
        "Supplier Risk", "Supply Chain", "Demand Planning", "Logistics",
        "Production Planning", "Scheduling", "Maintenance", "Warehouse", "Replenishment",
    ),
    "Legal, Security, Science & Personal": (
        "Legal Research", "Contract Analysis", "Clause Analysis", "Regulatory Compliance",
        "Policy Analysis", "Due Diligence", "Intellectual Property", "Risk Analysis",
        "Security Analysis", "Application Security", "Threat Intelligence", "Vulnerability",
        "SOC Analysis", "Security Incident", "Identity and Access", "Privacy Review",
        "Secret Detection", "Mathematics", "Symbolic Mathematics", "Numerical Computing",
        "Proof", "Optimization", "Simulation", "Scientific Reasoning", "Hypothesis",
        "Experiment Design", "Scientific Computing", "Reproducibility", "Engineering",
        "Tutor", "Teaching Assistant", "Course Design", "Study Planning", "Assessment",
        "Learning Analytics", "Email", "Calendar", "Meeting Notes", "Action Items",
        "Task Management", "Travel", "Personal Assistant",
    ),
}


READ_TOOLS = (
    "filesystem.list", "filesystem.metadata", "filesystem.search", "filesystem.read",
)
WRITE_TOOLS = READ_TOOLS + (
    "filesystem.mkdir", "filesystem.create", "filesystem.write", "filesystem.append",
    "filesystem.copy", "filesystem.move", "filesystem.trash", "filesystem.restore",
)


def _builtin_tool_policy(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    read_roles = {
        "Retrieval Agent", "Evidence Manager", "Verification Agent", "Quality Reviewer",
        "Provenance Agent", "Audit Agent", "File Search", "Document Extraction",
        "Document Comparison", "Document Metadata", "Repository Analysis",
        "Code Review", "Security Code Review", "Log Analysis", "Data Analysis",
        "Spreadsheet Analysis", "PDF", "Word Document", "LaTeX",
    }
    write_roles = {
        "Artifact Builder", "File Manager", "Coding", "Feature Development",
        "Debugging", "Refactoring", "Migration", "Build Engineering",
        "Developer Documentation", "Git", "Office Automation", "Document Conversion",
        "General Writing", "Editing", "Report Writing", "Proposal Writing",
        "Presentation", "Spreadsheet",
    }
    command_roles = {
        "Coding", "Feature Development", "Debugging", "Refactoring", "Unit Testing",
        "Integration Testing", "Quality Assurance", "Build Engineering", "Git",
        "CI/CD", "DevOps", "Environment Setup", "Log Analysis",
    }
    application_roles = {"Browser", "Browser Navigation", "Web Testing", "Desktop Control", "Application Control", "Office Automation"}
    tools: list[str] = []
    permissions: list[str] = []
    if name in read_roles or name in write_roles or name in command_roles:
        tools.extend(READ_TOOLS)
        permissions.append("active_read_path_grant")
    if name in write_roles or name in command_roles:
        tools.extend(WRITE_TOOLS[len(READ_TOOLS):])
        permissions.extend(("active_write_path_grant", "exact_approval_for_mutation"))
    if name in command_roles:
        tools.append("command.inspect")
        permissions.append("exact_command_approval")
    if name in application_roles:
        tools.append("application.control.propose")
        permissions.append("application_proposal_only")
    return tuple(dict.fromkeys(tools)), tuple(dict.fromkeys(permissions))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def built_in_agents() -> tuple[AgentTemplate, ...]:
    core = []
    manager_names = {
        "Meta-Controller", "Agent Economy Manager", "Agent Governor", "Nano Scheduler",
        "Background Task Manager", "Automation Manager", "Escalation Manager",
    }
    for name in CORE_AGENT_NAMES:
        agent_class = (
            AgentClass.META_CONTROLLER if name == "Meta-Controller"
            else AgentClass.SQUAD_MANAGER if name in manager_names
            else AgentClass.NANO if "Nano" in name
            else AgentClass.SPECIALIST
        )
        allowed_tools, permissions = _builtin_tool_policy(name)
        core.append(AgentTemplate(
            id=f"core.{_slug(name)}", name=name,
            description=f"Permanent AMCA control role: {name}.", category="Core",
            agent_class=agent_class,
            allowed_tools=allowed_tools,
            permissions=permissions,
            can_spawn=name in manager_names,
            max_children=32 if name in manager_names else 0,
        ))
    specialists = []
    for category, names in SPECIALIST_CATEGORIES.items():
        for name in names:
            allowed_tools, permissions = _builtin_tool_policy(name)
            specialists.append(AgentTemplate(
                id=f"specialist.{_slug(name)}", name=f"{name} Specialist",
                description=f"Reusable {category.casefold()} specialist for {name.casefold()} tasks.",
                category=category, agent_class=AgentClass.SPECIALIST,
                allowed_tools=allowed_tools, permissions=permissions,
            ))
    return tuple(core + specialists)


BUILT_IN_AGENTS = built_in_agents()


@dataclass(frozen=True, slots=True)
class AgentFabricSettings:
    auto_agents: bool = True
    dynamic_agents: bool = True
    nano_agents: bool = True
    recursive_spawning: bool = False
    speculative_execution: bool = False
    scaling: str = "balanced"
    activity_detail: str = "compact"
    envelope: ResourceEnvelope = ResourceEnvelope()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value


class AgentGovernor:
    """Hard deterministic authorization boundary for proposed organizations and dynamic child tasks."""

    @staticmethod
    def authorize(
        proposed_agents: int,
        proposed_concurrency: int,
        proposed_depth: int,
        estimated_api_cost_usd: float,
        settings: AgentFabricSettings,
    ) -> dict[str, Any]:
        envelope = settings.envelope
        checks = {
            "logical_agents": proposed_agents <= envelope.max_logical_agents,
            "concurrency": proposed_concurrency <= envelope.max_concurrent_agents,
            "manager_depth": proposed_depth <= envelope.max_manager_depth,
            "api_budget": estimated_api_cost_usd <= envelope.max_api_cost_usd,
            "recursive_spawning": proposed_depth <= 1 or settings.recursive_spawning,
        }
        return {
            "authorized": all(checks.values()),
            "checks": checks,
            "decision_layer": "deterministic_agent_governor",
        }

    @staticmethod
    def authorize_child_proposal(
        child: Any,
        *,
        parent_task: Any,
        org: Any,
        envelope: ResourceEnvelope | None = None,
        settings: AgentFabricSettings | None = None,
        available_adapters: Mapping[str, Any] | None = None,
        authorized_siblings: set[str] | None = None,
        denied_siblings: set[str] | None = None,
    ) -> dict[str, Any]:
        """Governor authorization for a dynamically proposed child task."""
        if envelope is None:
            envelope = ResourceEnvelope()
        if settings is None:
            settings = AgentFabricSettings(envelope=envelope)

        if hasattr(child, "id"):
            child_id = str(child.id)
            executor_kind = str(child.executor_kind)
            depends_on = tuple(child.depends_on)
            verifier_ids = tuple(child.verifier_ids)
            participant_ids = tuple(getattr(child, "participant_ids", ()))
            manager_depth = int(child.manager_depth)
        elif isinstance(child, Mapping):
            child_id = str(child.get("id", ""))
            executor_kind = str(child.get("executor_kind", ""))
            depends_on = tuple(child.get("depends_on", ()))
            verifier_ids = tuple(child.get("verifier_ids", ()))
            participant_ids = tuple(child.get("participant_ids", ()))
            manager_depth = int(child.get("manager_depth", 1))
        else:
            child_id = getattr(child, "id", "")
            executor_kind = getattr(child, "executor_kind", "")
            depends_on = tuple(getattr(child, "depends_on", ()))
            verifier_ids = tuple(getattr(child, "verifier_ids", ()))
            participant_ids = tuple(getattr(child, "participant_ids", ()))
            manager_depth = int(getattr(child, "manager_depth", 1))

        if hasattr(parent_task, "id"):
            manager_id = str(parent_task.id)
            parent_status = getattr(parent_task, "status", None)
            parent_error = getattr(parent_task, "error", None)
        elif isinstance(parent_task, Mapping):
            manager_id = str(parent_task.get("id", ""))
            parent_status = parent_task.get("status")
            parent_error = parent_task.get("error")
        else:
            manager_id = str(getattr(parent_task, "id", ""))
            parent_status = getattr(parent_task, "status", None)
            parent_error = getattr(parent_task, "error", None)
        # 1. Unique task ID check
        tasks_map = getattr(org, "tasks", {})
        auth_sibs = authorized_siblings or set()
        if child_id in tasks_map or child_id in auth_sibs:
            return {
                "authorized": False,
                "child_id": child_id,
                "manager_id": manager_id,
                "governor_rule": "unique_task_id",
                "reason": f"task with id {child_id!r} is already present in task graph",
                "authorized_at": _now(),
                "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
            }

        # 2. Parent Manager state check
        if parent_status not in {"completed", "merged", "running"} or parent_error is not None:
            return {
                "authorized": False,
                "child_id": child_id,
                "manager_id": manager_id,
                "governor_rule": "parent_manager_success",
                "reason": f"parent manager task {manager_id!r} did not succeed (status={parent_status!r}, error={parent_error!r})",
                "authorized_at": _now(),
                "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
            }

        # 2. Organization lifecycle check
        if getattr(org, "retired", False):
            return {
                "authorized": False,
                "child_id": child_id,
                "manager_id": manager_id,
                "governor_rule": "organization_active",
                "reason": "organization is already retired or terminal",
                "authorized_at": _now(),
                "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
            }

        # Check if org is in cancelled state
        tasks_map = getattr(org, "tasks", {})
        if any(getattr(t, "status", None) == "cancelled" and "organization cancelled" in str(getattr(t, "error", "")) for t in tasks_map.values()):
            return {
                "authorized": False,
                "child_id": child_id,
                "manager_id": manager_id,
                "governor_rule": "organization_not_cancelled",
                "reason": "organization has been cancelled",
                "authorized_at": _now(),
                "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
            }

        # 3. Logical agent cap check
        active_count = len(tasks_map)
        if active_count + 1 > envelope.max_logical_agents:
            return {
                "authorized": False,
                "child_id": child_id,
                "manager_id": manager_id,
                "governor_rule": "max_logical_agents",
                "reason": f"logical agent count ({active_count + 1}) exceeds envelope limit ({envelope.max_logical_agents})",
                "authorized_at": _now(),
                "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
            }

        # 4. Executor kind validation
        valid_kinds = {
            "deterministic_tool",
            "nano_local_model",
            "verification_cell",
            "manager",
            "collaboration",
        }
        if executor_kind not in valid_kinds:
            return {
                "authorized": False,
                "child_id": child_id,
                "manager_id": manager_id,
                "governor_rule": "valid_executor_kind",
                "reason": f"executor kind {executor_kind!r} is unpermitted or unknown",
                "authorized_at": _now(),
                "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
            }

        # 5. Manager recursive spawning & depth check
        parent_depth = int(
            getattr(parent_task, "manager_depth", 0)
            if hasattr(parent_task, "manager_depth")
            else parent_task.get("manager_depth", 0)
            if isinstance(parent_task, Mapping)
            else 0
        )
        canonical_depth = parent_depth + 1
        if hasattr(child, "manager_depth"):
            try:
                object.__setattr__(child, "manager_depth", canonical_depth)
            except Exception:
                child.manager_depth = canonical_depth

        if executor_kind == "manager":
            if not settings.recursive_spawning:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "recursive_spawning",
                    "reason": "recursive manager spawning is disabled by policy",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }
            if canonical_depth > envelope.max_manager_depth:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "max_manager_depth",
                    "reason": f"manager depth ({canonical_depth}) exceeds envelope limit ({envelope.max_manager_depth})",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }

        # 6. Dependency validation & transitive cycle prevention
        auth_sibs = authorized_siblings or set()
        den_sibs = denied_siblings or set()

        dep_graph: dict[str, set[str]] = {}
        for t_id, t_rec in tasks_map.items():
            dep_graph[t_id] = set(
                getattr(t_rec, "depends_on", ())
                if hasattr(t_rec, "depends_on")
                else t_rec.get("depends_on", ())
            )

        for dep in depends_on:
            if dep == child_id:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "no_cycle",
                    "reason": f"dependency {dep!r} introduces self-cycle",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }
            if dep in den_sibs:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "dependency_valid",
                    "reason": f"upstream dependency {dep!r} was denied",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }
            if dep not in tasks_map and dep not in auth_sibs:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "dependency_valid",
                    "reason": f"upstream dependency {dep!r} is unknown or not authorized",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }

        # Transitive cycle reachability check
        visited: set[str] = set()
        queue = list(depends_on)
        while queue:
            curr = queue.pop(0)
            if curr == child_id:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "no_cycle",
                    "reason": f"indirect cycle detected involving {child_id!r}",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }
            if curr not in visited:
                visited.add(curr)
                if curr in dep_graph:
                    queue.extend(dep_graph[curr])

        # 7. Verification Cell check
        if executor_kind == "verification_cell":
            if len(verifier_ids) < 2:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "verification_cell_min_verifiers",
                    "reason": f"verification cell requires >= 2 verifier_ids, found {len(verifier_ids)}",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }
            if available_adapters is not None:
                missing = [v for v in verifier_ids if v not in available_adapters]
                if missing:
                    return {
                        "authorized": False,
                        "child_id": child_id,
                        "manager_id": manager_id,
                        "governor_rule": "verification_cell_adapter_available",
                        "reason": f"requested verifier adapter(s) unavailable: {', '.join(missing)}",
                        "authorized_at": _now(),
                        "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                    }

        # 8. Collaboration task check
        if executor_kind == "collaboration":
            if len(participant_ids) < 2:
                return {
                    "authorized": False,
                    "child_id": child_id,
                    "manager_id": manager_id,
                    "governor_rule": "collaboration_min_participants",
                    "reason": f"collaboration task requires >= 2 participant_ids, found {len(participant_ids)}",
                    "authorized_at": _now(),
                    "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                }
            if available_adapters is not None:
                missing = [p for p in participant_ids if p not in available_adapters]
                if missing:
                    return {
                        "authorized": False,
                        "child_id": child_id,
                        "manager_id": manager_id,
                        "governor_rule": "collaboration_adapter_available",
                        "reason": f"requested collaboration participant adapter(s) unavailable: {', '.join(missing)}",
                        "authorized_at": _now(),
                        "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
                    }

        return {
            "authorized": True,
            "child_id": child_id,
            "manager_id": manager_id,
            "governor_rule": "envelope_and_safety_passed",
            "reason": "all resource and safety bounds satisfied",
            "authorized_at": _now(),
            "envelope_snapshot": envelope.to_dict() if hasattr(envelope, "to_dict") else {},
        }

    @staticmethod
    def authorize_tool(tool_id: str, assigned_tools: Sequence[str]) -> dict[str, Any]:
        assigned = tuple(dict.fromkeys(str(item) for item in assigned_tools))
        return {
            "authorized": tool_id in assigned,
            "tool_id": tool_id,
            "assigned_tools": list(assigned),
            "decision_layer": "deterministic_agent_governor",
            "reason": "capability assigned" if tool_id in assigned else "tool capability not assigned",
        }


class AgentToolRouter:
    """Proves capability assignment before the Permission Agent checks grants."""

    @staticmethod
    def authorize(agent_id: str, tool_id: str, assigned_tools: Sequence[str]) -> dict[str, Any]:
        governor = AgentGovernor.authorize_tool(tool_id, assigned_tools)
        return {
            "authorized": governor["authorized"],
            "agent_id": agent_id,
            "tool_id": tool_id,
            "governor": governor,
            "decision_layer": "deterministic_tool_router",
        }


class AgentEconomyManager:
    """Auditable first-generation scale selection with deterministic thresholds."""

    @staticmethod
    def recommend(goal: str, settings: AgentFabricSettings) -> dict[str, Any]:
        words = re.findall(r"[A-Za-z0-9_+-]+", goal)
        lowered = goal.casefold()
        domains = sum(token in lowered for token in (
            "research", "code", "test", "document", "spreadsheet", "browser",
            "deploy", "analyze", "compare", "citation", "image", "video", "database",
        ))
        if not settings.auto_agents or len(words) < 12:
            size, workers, managers, nanos = "minimal", 1, 0, 0
        elif len(words) < 45 and domains < 3:
            size, workers, managers, nanos = "small", 3, 0, 4
        elif len(words) < 140 and domains < 6:
            size, workers, managers, nanos = "medium", 8, 1, 16
        else:
            size, workers, managers, nanos = "large", 20, 3, 64
        scale = {"conservative": 0.6, "balanced": 1.0, "aggressive": 1.5}.get(settings.scaling, 1.0)
        workers = max(1, min(settings.envelope.max_logical_agents, round(workers * scale)))
        nanos = min(settings.envelope.max_nano_tasks, round(nanos * scale)) if settings.nano_agents else 0
        managers = min(managers, settings.envelope.max_manager_depth)
        return {
            "organization_size": size, "logical_workers": workers,
            "manager_depth": managers, "nano_tasks": nanos,
            "reason": "Smallest deterministic organization matching estimated task breadth.",
        }


class AgentFabricStore:
    """SQLite-backed custom agents, user controls, and local plan audit."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_fabric_settings (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), settings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS custom_agents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    category TEXT NOT NULL, instructions TEXT NOT NULL, model_policy TEXT NOT NULL,
                    allowed_tools_json TEXT NOT NULL, permissions_json TEXT NOT NULL,
                    output_schema TEXT NOT NULL, success_criteria TEXT NOT NULL,
                    can_spawn INTEGER NOT NULL, max_children INTEGER NOT NULL,
                    enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_organization_plans (
                    id TEXT PRIMARY KEY, project_id TEXT, goal_summary TEXT NOT NULL,
                    plan_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_capability_registry (
                    agent_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    specializations_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_capability_project ON agent_capability_registry(project_id);
                """
            )
            row = db.execute("SELECT 1 FROM agent_fabric_settings WHERE singleton=1").fetchone()
            if row is None:
                defaults = AgentFabricSettings().to_dict()
                db.execute("INSERT INTO agent_fabric_settings VALUES (1, ?, ?)", (json.dumps(defaults), _now()))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def settings(self) -> AgentFabricSettings:
        with self._connect() as db:
            raw = json.loads(db.execute("SELECT settings_json FROM agent_fabric_settings WHERE singleton=1").fetchone()[0])
        envelope = ResourceEnvelope(**raw.pop("envelope"))
        return AgentFabricSettings(**raw, envelope=envelope)

    def update_settings(self, values: Mapping[str, Any]) -> AgentFabricSettings:
        current = self.settings().to_dict()
        envelope_values = dict(current["envelope"])
        if isinstance(values.get("envelope"), Mapping):
            envelope_values.update(values["envelope"])
        for key in ("auto_agents", "dynamic_agents", "nano_agents", "recursive_spawning", "speculative_execution", "scaling", "activity_detail"):
            if key in values:
                current[key] = values[key]
        if current["scaling"] not in {"conservative", "balanced", "aggressive", "custom"}:
            raise ValueError("invalid scaling policy")
        if current["activity_detail"] not in {"compact", "detailed"}:
            raise ValueError("invalid activity detail policy")
        settings = AgentFabricSettings(
            auto_agents=bool(current["auto_agents"]), dynamic_agents=bool(current["dynamic_agents"]),
            nano_agents=bool(current["nano_agents"]), recursive_spawning=bool(current["recursive_spawning"]),
            speculative_execution=bool(current["speculative_execution"]), scaling=str(current["scaling"]),
            activity_detail=str(current["activity_detail"]), envelope=ResourceEnvelope(**envelope_values),
        )
        with self._transaction() as db:
            db.execute("UPDATE agent_fabric_settings SET settings_json=?, updated_at=? WHERE singleton=1", (json.dumps(settings.to_dict()), _now()))
        return settings

    def custom_agents(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM custom_agents ORDER BY name COLLATE NOCASE").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["allowed_tools"] = json.loads(item.pop("allowed_tools_json"))
            item["permissions"] = json.loads(item.pop("permissions_json"))
            item["preferred_model_policy"] = item.pop("model_policy")
            item["can_spawn"] = bool(item["can_spawn"])
            item["enabled"] = bool(item["enabled"])
            item["agent_class"] = AgentClass.SPECIALIST.value
            item["origin"] = "custom"
            result.append(item)
        return result

    def save_custom_agent(self, values: Mapping[str, Any], *, agent_id: str | None = None) -> dict[str, Any]:
        name = _clean(values.get("name"), name="name", maximum=120)
        description = _clean(values.get("description"), name="description", maximum=500)
        instructions = _clean(values.get("instructions"), name="instructions", maximum=8000)
        category = _clean(values.get("category") or "Custom", name="category", maximum=80)
        allowed_tools = values.get("allowed_tools") if isinstance(values.get("allowed_tools"), list) else []
        permissions = values.get("permissions") if isinstance(values.get("permissions"), list) else []
        if not all(isinstance(item, str) and item.strip() for item in [*allowed_tools, *permissions]):
            raise ValueError("tools and permissions must be string arrays")
        max_children = int(values.get("max_children") or 0)
        if not 0 <= max_children <= 64:
            raise ValueError("max_children must be between 0 and 64")
        agent_id = agent_id or f"custom.{uuid4().hex[:24]}"
        timestamp = _now()
        with self._transaction() as db:
            existing = db.execute("SELECT created_at FROM custom_agents WHERE id=?", (agent_id,)).fetchone()
            created_at = str(existing["created_at"]) if existing else timestamp
            db.execute(
                """INSERT OR REPLACE INTO custom_agents VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, name, description, category, instructions,
                 str(values.get("preferred_model_policy") or "auto_intelligence"),
                 json.dumps(list(dict.fromkeys(allowed_tools))), json.dumps(list(dict.fromkeys(permissions))),
                 _clean(values.get("output_schema") or "structured_result", name="output_schema", maximum=1000),
                 _clean(values.get("success_criteria") or "Objective completed with verifiable evidence.", name="success_criteria", maximum=1000),
                 int(bool(values.get("can_spawn", False))), max_children,
                 int(bool(values.get("enabled", True))), created_at, timestamp),
            )
        return next(item for item in self.custom_agents() if item["id"] == agent_id)

    def delete_custom_agent(self, agent_id: str) -> None:
        with self._transaction() as db:
            if not db.execute("DELETE FROM custom_agents WHERE id=?", (agent_id,)).rowcount:
                raise ValueError("custom agent not found")

    def agent_template(self, agent_id: str) -> dict[str, Any] | None:
        for agent in BUILT_IN_AGENTS:
            if agent.id == agent_id:
                return agent.to_dict()
        return next(
            (agent for agent in self.custom_agents() if agent["id"] == agent_id),
            None,
        )

    def tool_capabilities(self, agent_id: str) -> tuple[str, ...]:
        agent = self.agent_template(agent_id)
        if agent is None or not bool(agent.get("enabled", True)):
            raise PermissionError("agent is unavailable or disabled")
        tools = agent.get("allowed_tools", [])
        if not isinstance(tools, (list, tuple)):
            raise ValueError("agent tool capabilities are invalid")
        return tuple(str(item) for item in tools)

    def catalog(
        self,
        *,
        active_organizations: Any | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        builtins = [agent.to_dict() for agent in BUILT_IN_AGENTS]
        custom = self.custom_agents()
        categories: dict[str, int] = {}
        for agent in [*builtins, *custom]:
            categories[agent["category"]] = categories.get(agent["category"], 0) + 1
        # PS1-13H: truthful active count from authoritative lifecycle.
        # Never hardcoded: derived from live organizations when supplied
        # (authoritative task states, crash-uncertain excluded, project
        # isolated), otherwise from the capability registry (0 when empty,
        # truthfully computed, not a literal).
        active_total = 0
        active_status: str = "unavailable"
        active_org: dict[str, Any] | None = None
        try:
            if active_organizations is not None:
                from amca.agent_observability import (
                    is_task_active,
                    resolve_active_organization,
                )

                orgs = list(active_organizations)
                if project_id is not None:
                    kept = []
                    for org in orgs:
                        if isinstance(org, Mapping):
                            pid = str(org.get("project_id", ""))
                        else:
                            pid = str(getattr(org, "project_id", ""))
                        if pid == str(project_id):
                            kept.append(org)
                    orgs = kept
                for org in orgs:
                    if isinstance(org, Mapping):
                        tasks = org.get("tasks", [])
                        pid = str(org.get("project_id", ""))
                    else:
                        tasks = getattr(org, "tasks", {})
                        pid = str(getattr(org, "project_id", ""))
                    values = list(tasks.values()) if isinstance(tasks, Mapping) else list(tasks or [])
                    if project_id is not None:
                        scoped = []
                        for task in values:
                            if isinstance(task, Mapping):
                                tpid = str(task.get("project_id", pid))
                            else:
                                tpid = str(getattr(task, "project_id", pid))
                            if tpid == str(project_id):
                                scoped.append(task)
                        values = scoped
                    active_total += sum(1 for t in values if is_task_active(t))
                resolved = resolve_active_organization(orgs, requesting_project_id=project_id)
                active_org = resolved["active_organization"]
                active_status = str(resolved["active_organization_status"])
            else:
                # Registry-derived truthful count (no live org tracking here).
                with self._connect() as db:
                    rows = db.execute("SELECT lifecycle_status FROM agent_capability_registry").fetchall()
                active_like = {"active", "queued", "starting", "running", "waiting", "verifying"}
                active_total = sum(
                    1 for r in rows if str(r["lifecycle_status"]).strip().casefold() in active_like
                )
                active_org = None
                active_status = "unavailable" if project_id is None else "none"
                if project_id is not None:
                    # Project-isolated registry count (no cross-project leak).
                    with self._connect() as db:
                        prows = db.execute(
                            "SELECT lifecycle_status FROM agent_capability_registry WHERE project_id=?",
                            (str(project_id),),
                        ).fetchall()
                    active_total = sum(
                        1 for r in prows if str(r["lifecycle_status"]).strip().casefold() in active_like
                    )
        except Exception:
            # Observability must never break catalog; fall back to
            # truthfully-unknown rather than fabricating.
            active_total = 0
            active_org = None
            active_status = "unknown"
        return {
            "agents": [*builtins, *custom],
            "counts": {
                "core": len(CORE_AGENT_NAMES),
                "built_in_specialists": len(builtins) - len(CORE_AGENT_NAMES),
                "custom": len(custom),
                "total_available": len(builtins) + len(custom),
                "active": active_total,
            },
            "categories": categories,
            "settings": self.settings().to_dict(),
            "hierarchy": ["deterministic_tools", "nano", "micro", "specialist", "squad_manager", "lead_manager", "meta_controller", "user"],
            "status": "ready",
            "default_policy": "auto_agents",
            "active_organization": active_org,
            "active_organization_status": active_status,
        }

    def plan(self, goal: str, *, project_id: str | None = None) -> dict[str, Any]:
        goal = _clean(goal, name="goal", maximum=16000)
        settings = self.settings()
        economy = AgentEconomyManager.recommend(goal, settings)
        proposed = economy["logical_workers"] + economy["manager_depth"]
        authorization = AgentGovernor.authorize(
            proposed, min(proposed, settings.envelope.max_concurrent_agents),
            economy["manager_depth"], 0.0, settings,
        )
        lowered = goal.casefold()
        # The organization is a logical task graph, not a synonym for model
        # count.  Keep the permanent control roles small, then add only the
        # domain specialists justified by the task.
        selected = ["core.task-planner"]
        # v1.1.8 Phase 4: the plan requires eight agent types to be usable:
        # research, coding, review, testing, bug finding, validation, security
        # and documentation.  Review, testing, bug-finding and security existed
        # in the catalog but no rule could select them, so they were
        # unreachable - a planned-role set could never contain them.
        # The more specific rules come first so a goal that names security work
        # selects the security specialist rather than only the generalist.
        keyword_roles = (
            # Stems, not whole words: "vulnerability" is not a substring of
            # "vulnerabilities", so the plural form missed the rule entirely.
            (("security", "vulnerab", "exploit", "cve", "threat", "secret"),
             "specialist.security-analysis"),
            (("review", "code review", "pull request", "refactor"), "specialist.code-review"),
            (("unit test", "integration test", "test suite", "test coverage", "pytest"),
             "specialist.unit-testing"),
            (("bug", "reproduce", "regression", "defect", "crash"), "specialist.bug-reproduction"),
            (("validate", "validation", "acceptance", "verify the"), "core.verification-agent"),
            (("documentation", "readme", "changelog", "user guide", "api docs"),
             "specialist.technical-writing"),
            (("research", "source", "citation", "literature"), "specialist.deep-research"),
            # "Write a Python function" selected no coding agent because none of
            # code/software/repository/implement appear in it.  The vocabulary
            # now covers how a coding task is actually phrased.
            (("code", "software", "repository", "implement", "function", "script",
              "program", "algorithm", "class", "module", "api endpoint", "compile"),
             "specialist.coding"),
            (("data", "spreadsheet", "statistics", "analyze"), "specialist.data-analysis"),
            (("document", "report", "word", "pdf", "latex"), "specialist.report-writing"),
            (("browser", "website", "web page"), "specialist.browser"),
        )
        known = {agent.id: agent for agent in BUILT_IN_AGENTS}
        for keywords, role in keyword_roles:
            if any(keyword in lowered for keyword in keywords) and role in known:
                selected.append(role)
        # v1.1.8: enabled custom agents were catalog-only - plan() never looked
        # at them, so an agent the user created could never take part in a run.
        # A custom agent now joins the organization when the goal names it or
        # matches the vocabulary of its name/description.
        for agent in self.custom_agents():
            if not agent.get("enabled", True):
                continue
            agent_id = str(agent.get("id") or "")
            if not agent_id or agent_id in known:
                continue
            vocabulary = [
                token
                for token in re.split(
                    r"[^a-z0-9]+",
                    f"{agent.get('name') or ''} {agent.get('description') or ''}".casefold(),
                )
                if len(token) >= 4
            ]
            if agent_id in goal or any(token in lowered for token in vocabulary):
                known[agent_id] = AgentTemplate(
                    id=agent_id,
                    name=str(agent.get("name") or agent_id),
                    description=str(agent.get("description") or ""),
                    category=str(agent.get("category") or "Custom"),
                    agent_class=AgentClass.SPECIALIST,
                    origin="custom",
                    preferred_model_policy=str(
                        agent.get("preferred_model_policy") or "auto_intelligence"
                    ),
                    allowed_tools=tuple(
                        str(item) for item in agent.get("allowed_tools") or ()
                    ),
                    permissions=tuple(
                        str(item) for item in agent.get("permissions") or ()
                    ),
                    instructions=str(agent.get("instructions") or ""),
                    can_spawn=bool(agent.get("can_spawn", False)),
                    max_children=int(agent.get("max_children") or 0),
                )
                selected.append(agent_id)
        local_tool_terms = (
            "file", "folder", "workspace", "repository", "repo", "code", "test",
            "git", "terminal", "command", "browser", "desktop", "application",
            "word", "pdf", "latex", "spreadsheet", "database",
        )
        local_access_required = any(term in lowered for term in local_tool_terms)
        if local_access_required:
            selected.extend((
                "core.agent-governor",
                "core.tool-router",
                "core.permission-agent",
            ))
        if economy["logical_workers"] > 1:
            selected.extend((
                "core.critic-agent",
                "core.verification-agent",
                "core.result-synthesizer",
            ))
        if economy["manager_depth"] > 0:
            selected.append("core.collaboration-router")
        selected = list(dict.fromkeys(selected))
        tool_assignments: list[dict[str, Any]] = [
            {
                "agent_id": "core.agent-governor",
                "allowed_tools": ["local_policy_snapshot"],
                "permissions": ["read_policy", "deny_or_authorize_proposal"],
            },
            {
                "agent_id": "core.permission-agent",
                "allowed_tools": ["approval_store", "path_grant_store"],
                "permissions": ["propose_only", "never_self_approve"],
            },
            {
                "agent_id": "core.tool-router",
                "allowed_tools": ["bounded_local_filesystem"],
                "permissions": ["active_project_profile", "active_path_grant", "exact_approval_when_required"],
            },
        ] if local_access_required else []
        for agent_id in selected:
            if not agent_id.startswith("specialist."):
                continue
            template = known[agent_id]
            tools = list(template.allowed_tools)
            permissions = list(template.permissions)
            tool_assignments.append({
                "agent_id": agent_id,
                "allowed_tools": tools,
                "permissions": permissions,
            })
        assigned_ids = {str(item["agent_id"]) for item in tool_assignments}
        for agent_id in selected:
            if agent_id in assigned_ids:
                continue
            template = known.get(agent_id)
            if template is None:
                continue
            tool_assignments.append({
                "agent_id": agent_id,
                "allowed_tools": list(template.allowed_tools),
                "permissions": list(template.permissions),
            })
        # v1.1.8 Phase 4: the graph used to admit only ids starting with
        # "specialist.", so every custom agent and every control role was
        # selected and then silently given no task.  A role that is planned but
        # never tasked is exactly the "skipped role" the plan names, so the
        # graph now covers every selected agent.
        _dedicated = {"core.task-planner", "core.critic-agent",
                      "core.verification-agent", "core.result-synthesizer"}
        _worker_roles = [item for item in selected if item not in _dedicated]
        task_graph = [
            {
                "id": "understand",
                "agent_id": "core.task-planner",
                "depends_on": [],
                "executor_class": "deterministic_or_cheapest_adequate",
                "status": "authorized",
            },
            *[
                {
                    "id": f"analyze-{index + 1}",
                    "agent_id": agent_id,
                    "depends_on": ["understand"],
                    "executor_class": "specialist",
                    "status": "authorized",
                }
                for index, agent_id in enumerate(_worker_roles)
            ],
            {
                "id": "critique",
                "agent_id": "core.critic-agent",
                "depends_on": ["understand"],
                "executor_class": "nano_or_model",
                "status": "authorized",
            },
            {
                "id": "verify",
                "agent_id": "core.verification-agent",
                "depends_on": ["critique"],
                "executor_class": "deterministic_first",
                "status": "authorized",
            },
            {
                "id": "synthesize",
                "agent_id": "core.result-synthesizer",
                "depends_on": ["verify"],
                "executor_class": "selected_model_set",
                "status": "authorized",
            },
        ] if economy["logical_workers"] > 1 else [
            *[
                {
                    "id": "complete" if index == 0 else f"complete-{index + 1}",
                    "agent_id": agent_id,
                    "depends_on": [] if index == 0 else ["complete"],
                    "executor_class": "cheapest_adequate",
                    "status": "authorized",
                }
                for index, agent_id in enumerate(selected)
            ]
        ]
        plan = {
            "id": f"organization.{uuid4().hex[:24]}", "project_id": project_id,
            "goal_summary": goal[:240], "economy": economy, "governor": authorization,
            "selected_agents": selected, "lifecycle": AgentLifecycle.AUTHORIZED.value if authorization["authorized"] else AgentLifecycle.PROPOSED.value,
            "task_graph": task_graph,
            "nano_policy": {
                "enabled": settings.nano_agents,
                "maximum": economy["nano_tasks"],
                "rule": "deterministic tools first; batch only independent bounded tasks",
            },
            "tool_policy": {
                "deny_by_default": True,
                "local_access_required": local_access_required,
                "deterministic_first": True,
                "control_agents": ["core.agent-governor", "core.permission-agent", "core.tool-router"] if local_access_required else [],
                "assignments": tool_assignments,
                "filesystem_executor": "enabled_only_with_active_profile_and_path_grant",
                "command_execution": "inspection_only_exact_approval",
                "application_control": "proposal_only",
                "event_contract": ["proposed", "waiting_for_approval", "authorized", "succeeded", "denied", "failed"],
            },
            "external_api_cost_usd": 0.0, "model_calls_started": False,
            "created_at": _now(),
        }
        with self._transaction() as db:
            db.execute("INSERT INTO agent_organization_plans VALUES (?, ?, ?, ?, ?)", (plan["id"], project_id, plan["goal_summary"], json.dumps(plan), plan["created_at"]))
        return plan

    def register_capability_agent(
        self,
        project_id: str,
        name: str,
        role: str,
        specializations: Sequence[str],
        capabilities: Sequence[str],
        *,
        agent_id: str | None = None,
        description: str = "",
        lifecycle_status: str = "active",
    ) -> dict[str, Any]:
        project_id = _clean(project_id, name="project_id", maximum=128)
        name = _clean(name, name="name", maximum=128)
        role = _clean(role, name="role", maximum=128)
        aid = agent_id or f"agent.{uuid4().hex[:16]}"
        now = _now()
        spec_list = list(dict.fromkeys(str(s).strip() for s in specializations if str(s).strip()))
        cap_list = list(dict.fromkeys(str(c).strip() for c in capabilities if str(c).strip()))

        with self._transaction() as db:
            existing = db.execute(
                "SELECT created_at FROM agent_capability_registry WHERE agent_id=?",
                (aid,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """INSERT OR REPLACE INTO agent_capability_registry VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    aid,
                    project_id,
                    name,
                    role,
                    json.dumps(spec_list),
                    json.dumps(cap_list),
                    lifecycle_status,
                    str(description or "").strip(),
                    created_at,
                    now,
                ),
            )
        return {
            "agent_id": aid,
            "project_id": project_id,
            "name": name,
            "role": role,
            "specializations": spec_list,
            "capabilities": cap_list,
            "lifecycle_status": lifecycle_status,
            "description": str(description or "").strip(),
            "created_at": created_at,
            "updated_at": now,
        }

    def list_capability_agents(self, project_id: str) -> list[dict[str, Any]]:
        project_id = _clean(project_id, name="project_id", maximum=128)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_capability_registry WHERE project_id=? ORDER BY created_at ASC",
                (project_id,),
            ).fetchall()
        return [
            {
                "agent_id": str(r["agent_id"]),
                "project_id": str(r["project_id"]),
                "name": str(r["name"]),
                "role": str(r["role"]),
                "specializations": json.loads(str(r["specializations_json"])),
                "capabilities": json.loads(str(r["capabilities_json"])),
                "lifecycle_status": str(r["lifecycle_status"]),
                "description": str(r["description"]),
                "created_at": str(r["created_at"]),
                "updated_at": str(r["updated_at"]),
            }
            for r in rows
        ]

    def find_agents_by_capability(self, project_id: str, capability: str) -> list[dict[str, Any]]:
        agents = self.list_capability_agents(project_id)
        cap = capability.strip().casefold()
        return [
            a for a in agents
            if any(cap == c.strip().casefold() for c in a["capabilities"])
        ]

    @staticmethod
    def score_agent_suitability(agent: Mapping[str, Any], task_description: str) -> float:
        task_words = set(re.findall(r"[a-z0-9]+", task_description.casefold()))
        if not task_words:
            return 0.0

        score = 0.0
        role_words = set(re.findall(r"[a-z0-9]+", str(agent.get("role", "")).casefold()))
        score += len(task_words & role_words) * 2.0

        for spec in agent.get("specializations", []):
            spec_words = set(re.findall(r"[a-z0-9]+", str(spec).casefold()))
            score += len(task_words & spec_words) * 1.5

        for cap in agent.get("capabilities", []):
            cap_words = set(re.findall(r"[a-z0-9]+", str(cap).casefold()))
            score += len(task_words & cap_words) * 1.0

        return round(min(10.0, score), 2)


__all__ = [
    "AgentCapabilityRecord", "AgentClass", "AgentEconomyManager", "AgentFabricSettings", "AgentFabricStore",
    "AgentGovernor", "AgentLifecycle", "AgentTemplate", "AgentToolRouter", "BUILT_IN_AGENTS",
    "CORE_AGENT_NAMES", "ResourceEnvelope", "SPECIALIST_CATEGORIES",
]
