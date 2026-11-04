"""AMCA Studio v1.0.0 upgrade / migration / backup-restore certification.

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2D-UPGRADE-MIGRATION-BACKUP-RESTORE-CERTIFICATION-01
Worker: OpenCode PS-2 (independent release-certification track, parallel-session safe)
Mode: test/documentation-first. This module makes NO production-code changes.

Certifies that an existing v0.9.0 data state upgrades to the current v1.0.0
candidate schema without loss or corruption, and that backup -> restore stays
safe across the upgrade boundary. The "canonical migration path" exercised here
is exactly what production does on startup: each durable store opens a
pre-existing database file and runs its guarded, additive migration
(CREATE TABLE IF NOT EXISTS + PRAGMA table_info / try-except ALTER TABLE +
explicit user_version gate in SQLiteStore).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from amca.agent_recovery import AgentRecoveryStore
from amca.artifacts import ArtifactError, ArtifactStore
from amca.automations import AutomationStore
from amca.chat_history import ConversationStore
from amca.cognitive_loop_orchestrator import (
    CognitiveWorkflowRecord,
    CognitiveWorkflowStore,
)
from amca.cognitive_runtime_bridge import (
    CognitiveRuntimeBridgeStore,
    RuntimeEvidenceRecord,
    RuntimeSessionRecord,
)
from amca.cognitive_runtime_observability import (
    CognitiveRuntimeObservabilityStore,
    RuntimeEventStreamItem,
    RuntimeFailureRecord,
)
from amca.credentials import WindowsDpapiCredentialStore
from amca.domain import ProjectState
from amca.exporting import (
    EXPORT_SCHEMA_VERSION,
    build_audit_export,
    redact_secrets,
)
from amca.knowledge_context_service import KnowledgeAuditEvent, KnowledgeAuditStore
from amca.knowledge_fts import KnowledgeFtsStore
from amca.knowledge_management import (
    KnowledgeAssetRecord,
    KnowledgeAssetStore,
    KnowledgeProvenance,
)
from amca.knowledge_vector import KnowledgeVectorStore
from amca.manual_model_selection_workflow_binding import (
    WorkflowModelBindingManager,
)
from amca.model_evaluation_evidence import (
    BenchmarkEvidenceRegistry,
    EvaluationExecutionEnvironment,
    EvaluationTaskCategory,
)
from amca.model_hub import ModelHubStore
from amca.model_profiles import ModelProfileStore
from amca.model_selection_workspace import ModelSelectionManager
from amca.operations_foundation import (
    BackupRestoreManager,
    DiagnosticsManager,
    UpdateReadinessManager,
    UpdateState,
)
from amca.permissions import PermissionStore
from amca.privacy import TransmissionStore
from amca.provider_governance import (
    CredentialGovernanceStore,
    compute_key_fingerprint,
)
from amca.research_context import ContextStore
from amca.research_orchestrator import ResearchLimits, ResearchRunStore
from amca.research_planning import PlanningTrailStore
from amca.research_source import ResearchStore
from amca.research_synthesis import ClaimType, ResearchSynthesisStore
from amca.state import StateManager
from amca.storage import SQLiteStore
from amca.studio_memory import StudioMemoryStore
from amca.workflow_optimization import (
    OptimizationRecommendation,
    WorkflowAnalysisRecord,
    WorkflowOptimizationStore,
)
from amca.workspace_governance import WorkspaceGovernanceStore

# --------------------------------------------------------------------------
# Fixture identity (deterministic; historically labelled v0.9.0 throughout)
# --------------------------------------------------------------------------

WS_A = "ws-i2d-alpha"
WS_B = "ws-i2d-beta"
PROJ_A = "proj-i2d-alpha"
PROJ_B = "proj-i2d-beta"
APP_VERSION_090 = "0.9.0"
TS = "2026-01-15T10:00:00+00:00"

SECRET_CANARY = "sk-proj-"  # must never appear in any persisted fixture/export

LEGACY_TABLES = [
    "research_runs",
    "research_run_steps",
    "studio_project_memories",
    "desktop_conversations",
    "desktop_conversation_messages",
    "desktop_automations",
    "desktop_automation_occurrences",
    "desktop_agent_organizations",
    "desktop_agent_tasks",
    "desktop_agent_call_attempts",
    "desktop_agent_reservations",
    "desktop_agent_verification_verdicts",
    "research_planning_turns",
    "research_planning_events",
    "research_retrieval_metrics",
]


def _create_v090_legacy_db(path: Path) -> None:
    """Craft a v0.9.0-shaped database with raw SQL (pre-migration shapes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    try:
        con.executescript(
            """
            CREATE TABLE research_runs (
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                session_id TEXT NOT NULL, objective TEXT NOT NULL,
                status TEXT NOT NULL, stop_reason TEXT,
                limits_json TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE research_run_steps (
                step_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL, action_type TEXT NOT NULL,
                arguments_json TEXT DEFAULT '{}', reason_code TEXT,
                decision TEXT, execution_ref TEXT,
                outcome TEXT DEFAULT 'pending',
                started_at TEXT, completed_at TEXT
            );
            CREATE TABLE studio_project_memories (
                memory_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                memory_type TEXT NOT NULL, title TEXT NOT NULL,
                content TEXT NOT NULL, source_ref TEXT,
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE desktop_conversations (
                conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('solo', 'joint')),
                participant_ids_json TEXT NOT NULL,
                project_id TEXT, active_run_id TEXT,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, last_message_at TEXT
            );
            CREATE TABLE desktop_conversation_messages (
                message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, meta TEXT, run_id TEXT,
                project_id TEXT, created_at TEXT NOT NULL,
                UNIQUE(conversation_id, ordinal),
                FOREIGN KEY(conversation_id)
                    REFERENCES desktop_conversations(conversation_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE desktop_automations (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                project_id TEXT NOT NULL, goal TEXT NOT NULL,
                automation_type TEXT NOT NULL, schedule TEXT NOT NULL,
                enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, last_run_at TEXT,
                last_run_id TEXT, metadata_json TEXT NOT NULL
            );
            CREATE TABLE desktop_automation_occurrences (
                id TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
                scheduled_for TEXT NOT NULL, status TEXT NOT NULL,
                claimed_at TEXT NOT NULL, run_id TEXT,
                completed_at TEXT, error TEXT,
                UNIQUE (automation_id, scheduled_for)
            );
            CREATE TABLE desktop_agent_organizations (
                organization_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                goal TEXT NOT NULL, budget_usd REAL NOT NULL,
                max_concurrent INTEGER NOT NULL, max_attempts INTEGER DEFAULT 2,
                status TEXT NOT NULL, retired INTEGER DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE desktop_agent_tasks (
                id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL
                    REFERENCES desktop_agent_organizations(organization_id),
                project_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                focus TEXT NOT NULL, status TEXT NOT NULL,
                recovery_disposition TEXT,
                external_outcome_uncertain INTEGER DEFAULT 0,
                executor_kind TEXT, depends_on_json TEXT,
                verifier_ids_json TEXT, participant_ids_json TEXT,
                collaboration_architecture TEXT, attempts INTEGER DEFAULT 0,
                result_summary TEXT, verification_result_json TEXT,
                error TEXT, cost_usd REAL DEFAULT 0.0,
                local_calls INTEGER DEFAULT 0, api_calls INTEGER DEFAULT 0,
                reused_from TEXT, reservation_id TEXT,
                proposed_children_json TEXT, proposed_by_manager_id TEXT,
                manager_depth INTEGER DEFAULT 0,
                authorization_record_json TEXT,
                created_at TEXT, started_at TEXT, completed_at TEXT
            );
            CREATE TABLE desktop_agent_call_attempts (
                call_attempt_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL
                    REFERENCES desktop_agent_organizations(organization_id),
                task_id TEXT NOT NULL
                    REFERENCES desktop_agent_tasks(id),
                task_attempt_number INTEGER NOT NULL,
                call_key TEXT NOT NULL, executor_kind TEXT,
                adapter_id TEXT, participant_id TEXT, phase_id TEXT,
                round_index INTEGER, work_unit_id TEXT, reservation_id TEXT,
                state TEXT, reserved_usd REAL DEFAULT 0.0,
                actual_cost_usd REAL DEFAULT 0.0, response_payload TEXT,
                provider_request_id TEXT, dispatch_intent_at TEXT,
                dispatched_at TEXT, response_received_at TEXT,
                response_committed_at TEXT, settled_at TEXT,
                recovery_disposition TEXT,
                UNIQUE(organization_id, task_id, task_attempt_number, call_key)
            );
            CREATE TABLE desktop_agent_reservations (
                reservation_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL
                    REFERENCES desktop_agent_organizations(organization_id),
                task_id TEXT, attempt INTEGER, amount_usd REAL,
                status TEXT, settled_actual_usd REAL DEFAULT 0.0,
                integrity_violation INTEGER DEFAULT 0,
                created_at TEXT, settled_at TEXT
            );
            CREATE TABLE desktop_agent_verification_verdicts (
                id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
                task_id TEXT NOT NULL, verifier_id TEXT NOT NULL,
                verdict TEXT NOT NULL, summary TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                UNIQUE(task_id, verifier_id)
            );
            CREATE TABLE research_planning_turns (
                turn_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL, model_provider_id TEXT,
                model_model_id TEXT, planning_source TEXT NOT NULL,
                state_json TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(run_id, turn_index)
            );
            CREATE TABLE research_planning_events (
                event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL, step_number INTEGER,
                candidate_index INTEGER NOT NULL,
                model_provider_id TEXT, model_model_id TEXT,
                candidate_json TEXT NOT NULL,
                validator_result TEXT NOT NULL, rejection_reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE research_retrieval_metrics (
                project_id TEXT NOT NULL, metric TEXT NOT NULL,
                value INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (project_id, metric)
            );
            """
        )
        # --- legacy rows: workspace/project A (alpha) ---------------------
        con.execute(
            "INSERT INTO research_runs VALUES (?,?,?,?,?,?,?,?,?)",
            ("run-legacy-a", PROJ_A, "rs-legacy-a", "Legacy alpha objective",
             "planned", None, '{"max_steps": 12}', TS, None),
        )
        con.execute(
            "INSERT INTO research_run_steps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("step-legacy-a", "run-legacy-a", 0, "search", "{}",
             "no_evidence", None, None, "pending", TS, None),
        )
        con.execute(
            "INSERT INTO studio_project_memories VALUES (?,?,?,?,?,?,?,?,?)",
            ("mem-legacy-a", PROJ_A, "fact", "Legacy memory",
             "The alpha workspace prefers local models.", None, 0, TS, TS),
        )
        con.execute(
            "INSERT INTO desktop_conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("conv-legacy-a", "Legacy chat", "joint", '["agent-1"]',
             PROJ_A, None, "idle", TS, TS, None),
        )
        con.execute(
            "INSERT INTO desktop_conversation_messages VALUES (?,?,?,?,?,?,?,?,?)",
            ("msg-legacy-a", "conv-legacy-a", 1, "user",
             "Legacy hello from alpha.", None, None, PROJ_A, TS),
        )
        con.execute(
            "INSERT INTO desktop_automations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("auto-legacy-a", "Legacy automation", PROJ_A,
             "Nightly alpha digest", "once", "manual", 1, TS, TS,
             None, None, "{}"),
        )
        # Interrupted claim: claimed but never dispatched (no run_id).
        con.execute(
            "INSERT INTO desktop_automation_occurrences VALUES (?,?,?,?,?,?,?,?)",
            ("occ-legacy-a", "auto-legacy-a", TS, "claimed", TS,
             None, None, None),
        )
        con.execute(
            "INSERT INTO desktop_agent_organizations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("org-legacy-a", PROJ_A, "Legacy alpha goal", 25.0, 2, 2,
             "running", 0, TS, TS),
        )
        con.execute(
            """INSERT INTO desktop_agent_tasks (
                   id, organization_id, project_id, agent_id, focus,
                   status, executor_kind, created_at
               ) VALUES (?,?,?,?,?,?,?,?)""",
            ("task-legacy-a", "org-legacy-a", PROJ_A, "agent-1",
             "Legacy alpha focus", "running", "local", TS),
        )
        con.execute(
            """INSERT INTO desktop_agent_call_attempts (
                   call_attempt_id, organization_id, task_id,
                   task_attempt_number, call_key, executor_kind,
                   adapter_id, state
               ) VALUES (?,?,?,?,?,?,?,?)""",
            ("call-legacy-a", "org-legacy-a", "task-legacy-a", 0,
             "call-key-a", "local", "adapter-local", "reserved"),
        )
        con.execute(
            "INSERT INTO desktop_agent_reservations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("resv-legacy-a", "org-legacy-a", "task-legacy-a", 0,
             1.5, "held", 0.0, 0, TS, None),
        )
        con.execute(
            "INSERT INTO desktop_agent_verification_verdicts VALUES (?,?,?,?,?,?,?,?,?)",
            ("verd-legacy-a", "org-legacy-a", "task-legacy-a", "verifier-1",
             "pass", "Legacy alpha verdict", "[]", 0.0, TS),
        )
        con.execute(
            "INSERT INTO research_planning_turns VALUES (?,?,?,?,?,?,?,?)",
            ("turn-legacy-a", "run-legacy-a", 0, "local",
             "ministral-3-14b", "legacy", '{"step": 0}', TS),
        )
        con.execute(
            "INSERT INTO research_planning_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("pev-legacy-a", "run-legacy-a", 0, 0, 0, "local",
             "ministral-3-14b", "{}", "accepted", None, TS),
        )
        con.execute(
            "INSERT INTO research_retrieval_metrics VALUES (?,?,?)",
            (PROJ_A, "searches", 3),
        )
        # --- legacy rows: workspace/project B (beta, isolation counterpart) --
        con.execute(
            "INSERT INTO studio_project_memories VALUES (?,?,?,?,?,?,?,?,?)",
            ("mem-legacy-b", PROJ_B, "fact", "Legacy beta memory",
             "The beta workspace tracks release evidence.", None, 1,
             TS, TS),
        )
        con.execute(
            "INSERT INTO desktop_conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("conv-legacy-b", "Legacy beta chat", "solo", '["agent-9"]',
             PROJ_B, None, "idle", TS, TS, None),
        )
        con.commit()
    finally:
        con.close()


def _table_names(db_path: Path) -> list[str]:
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return sorted(r[0] for r in rows)
    finally:
        con.close()


def _table_columns(db_path: Path, table: str) -> list[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def _dump_table(db_path: Path, table: str) -> list[dict]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
        dumped = [dict(r) for r in rows]
        return sorted(dumped, key=lambda d: json.dumps(d, sort_keys=True, default=str))
    finally:
        con.close()


def _dump_db(db_path: Path) -> dict[str, list[dict]]:
    return {t: _dump_table(db_path, t) for t in _table_names(db_path)}


def _open_all_stores(db_path: Path, payloads_root: Path) -> dict:
    """Open every durable store on the fixture DB (runs all migrations).

    NOTE (I2D.1): ConversationStore is opened here. A pre-C1 v0.9 shape used
    to crash its startup migration (I2D-001); the ordered repair in
    ConversationStore.__init__ (ALTER before rebuild) now migrates it.
    See test_pre_c1_conversations_shape_migration (regression) and
    test_conversations_migration_variants.
    """
    stores = {
        "core": SQLiteStore(db_path),
        "runs": ResearchRunStore(db_path),
        "memory": StudioMemoryStore(db_path),
        "conversations": ConversationStore(db_path),
        "automations": AutomationStore(db_path),
        "recovery": AgentRecoveryStore(db_path),
        "planning": PlanningTrailStore(db_path),
        "research": ResearchStore(db_path),
        "synthesis": ResearchSynthesisStore(db_path),
        "context": ContextStore(db_path),
        "knowledge": KnowledgeAssetStore(db_path),
        "fts": KnowledgeFtsStore(db_path),
        "vectors": KnowledgeVectorStore(db_path),
        "audit": KnowledgeAuditStore(db_path),
        "artifacts": ArtifactStore(database=db_path, payloads_root=payloads_root),
        "permissions": PermissionStore(db_path),
        "transmissions": TransmissionStore(db_path),
        "profiles": ModelProfileStore(db_path),
        "hub": ModelHubStore(db_path),
        "credgov": CredentialGovernanceStore(db_path),
        "cogworkflows": CognitiveWorkflowStore(db_path),
        "runtime": CognitiveRuntimeBridgeStore(db_path),
        "observability": CognitiveRuntimeObservabilityStore(db_path),
        "workflowopt": WorkflowOptimizationStore(db_path),
        "workspacegov": WorkspaceGovernanceStore(db_path),
    }
    return stores


def _close_all_stores(stores: dict) -> None:
    for store in stores.values():
        close = getattr(store, "close", None)
        if callable(close):
            close()


def _seed_current_shape_records(stores: dict) -> dict:
    """Seed v0.9-era records through current APIs (both workspaces)."""
    seeded: dict[str, object] = {}
    core = stores["core"]
    core.save_state(ProjectState(project_id=PROJ_A, goal="Alpha workspace goal"))
    core.save_state(ProjectState(project_id=PROJ_B, goal="Beta workspace goal"))
    core.reserve_model_call(
        reservation_id="res-i2d-a",
        project_id=PROJ_A,
        owner_id="owner-i2d-a",
        adapter_id="adapter-local",
        is_api=True,
        estimated_cost_usd=0.02,
        recorded_api_cost_usd=0.0,
        budget_usd=None,
    )

    mem = stores["memory"]
    mem.save_memory(project_id=PROJ_A, title="Alpha note",
                    content="Alpha release checklist item.", memory_id="mem-i2d-a")
    mem.save_memory(project_id=PROJ_B, title="Beta note",
                    content="Beta rollback procedure pointer.", memory_id="mem-i2d-b")

    know = stores["knowledge"]
    for ws, aid, title in ((WS_A, "k-i2d-a", "Alpha architecture note"),
                           (WS_B, "k-i2d-b", "Beta test plan")):
        know.save_asset(KnowledgeAssetRecord(
            asset_id=aid, workspace_id=ws, title=title,
            asset_type="document", source_ref="i2d-fixture",
            collections=("i2d",), tags=("fixture",), is_shared=False,
            provenance=KnowledgeProvenance(
                originating_task_id="task-i2d", generating_agent_id="agent-i2d",
                model_decision_id="sel-i2d", workflow_id="wf-i2d",
                created_at=TS),
        ))
    fts = stores["fts"]
    fts.index_artifact(artifact_id="k-i2d-a", workspace_id=WS_A,
                       title="Alpha architecture note",
                       content="alpha reusable reusable token")
    fts.index_artifact(artifact_id="k-i2d-b", workspace_id=WS_B,
                       title="Beta test plan", content="beta isolated token")
    vec = stores["vectors"]
    vec.store_vector(artifact_id="k-i2d-a", workspace_id=WS_A,
                     model_name="amca-offline", vector=[0.1, 0.2, 0.3])
    vec.store_vector(artifact_id="k-i2d-b", workspace_id=WS_B,
                     model_name="amca-offline", vector=[0.4, 0.5, 0.6])
    audit = stores["audit"]
    for ws in (WS_A, WS_B):
        audit.record_event(KnowledgeAuditEvent(
            event_id=f"kae-i2d-{ws[-5:]}", workspace_id=ws,
            requester_role="researcher", action="knowledge.search",
            query_intent="i2d fixture probe",
            retrieved_artifact_ids=(f"k-i2d-{ws[-5]}",),
            permission_decision="allow", timestamp=TS))

    art = stores["artifacts"]
    doc_a = art.author(project_id=PROJ_A, artifact_type="markdown",
                       title="Alpha design", content="# Alpha v0.9 design",
                       created_by="op-i2d")
    art.edit(artifact_id=doc_a["artifact_id"], content="# Alpha v0.9 design\n\nAmended.",
             created_by="op-i2d", base_version=1)
    doc_b = art.author(project_id=PROJ_B, artifact_type="markdown",
                       title="Beta design", content="# Beta v0.9 design",
                       created_by="op-i2d")
    seeded["artifact_a"] = doc_a["artifact_id"]
    seeded["artifact_b"] = doc_b["artifact_id"]

    conv = stores["conversations"]
    conv.create(title="Alpha adaptive chat", mode="adaptive_hybrid",
                participant_ids=["agent-1", "agent-2"],
                conversation_id="conv-i2d-a", workspace_project_id=WS_A)
    conv.add_message("conv-i2d-a", message_id="msg-i2d-a", role="user",
                     content="Alpha status check.", project_id=PROJ_A)
    conv.create(title="Beta chat", mode="solo", participant_ids=["agent-9"],
                conversation_id="conv-i2d-b", workspace_project_id=WS_B)

    research = stores["research"]
    sess = research.create_session(project_id=PROJ_A, title="Alpha evidence session")
    research.create_source(sess["session_id"], source_kind="web",
                           canonical_origin="https://example.com/alpha",
                           title="Alpha source")
    seeded["research_session_a"] = sess["session_id"]

    runs = stores["runs"]
    run = runs.create_run(project_id=PROJ_A, session_id=sess["session_id"],
                          objective="Alpha run objective",
                          limits=ResearchLimits())
    runs.update_planner_metadata(run["run_id"], planner_mode="deterministic",
                                 planner_source="i2d-fixture")

    planning = stores["planning"]
    planning.save_turn(run_id=run["run_id"], turn_index=0, model=None,
                       planning_source="i2d-fixture", state={"step": 0},
                       candidates=[], mode="deterministic")

    synth = stores["synthesis"]
    question = synth.create_question(session_id=sess["session_id"],
                                     project_id=PROJ_A,
                                     question="Does the alpha workflow preserve provenance?")
    claim = synth.create_claim(
        session_id=sess["session_id"], project_id=PROJ_A,
        claim_text="The alpha workflow preserves provenance across migration.",
        claim_type=ClaimType.FACTUAL, question_id=question["question_id"])
    seeded["claim_a"] = claim["claim_id"]

    ctx = stores["context"]
    ctx.save_package({"context_id": "ctx-i2d-a", "project_id": PROJ_A,
                      "session_id": sess["session_id"], "purpose": "i2d",
                      "status": "active", "digest": "digest-i2d-a",
                      "package_json": '{"alpha": true}',
                      "created_at": TS})

    auto = stores["automations"]
    auto.save({"id": "auto-i2d-a", "name": "Alpha digest",
               "project_id": PROJ_A, "goal": "Daily alpha digest",
               "automation_type": "once", "enabled": True})

    rec = stores["recovery"]
    rec.create_organization(organization_id="org-i2d-a", project_id=PROJ_A,
                            goal="Alpha recovery goal", budget_usd=10.0,
                            max_concurrent=2)
    rec.create_task(task_id="task-i2d-a", organization_id="org-i2d-a",
                    project_id=PROJ_A, agent_id="agent-1",
                    focus="Alpha focus", executor_kind="local")

    perms = stores["permissions"]
    scope = {"resource": "doc-i2d-a", "operation": "read"}
    approval = perms.request(project_id=PROJ_A, action="read_asset",
                             scope=scope, reason="I2D fixture approval",
                             request_id="appr-i2d-a")
    perms.decide("appr-i2d-a", approve=True, decided_by="op-i2d")
    tx_scope = {"adapter_id": "adapter-local", "provider": "local",
                "model": "ministral-3-14b",
                "excerpt_sha256": "0" * 64, "excerpt_utf8_bytes": 12,
                "system_prompt_sha256": "1" * 64,
                "template_version": "private-hybrid-excerpt-v1"}
    tx_approval = perms.request(project_id=PROJ_A,
                                action="transmit_excerpt", scope=tx_scope,
                                reason="I2D fixture transmission",
                                request_id="appr-i2d-tx")
    perms.decide("appr-i2d-tx", approve=True, decided_by="op-i2d")
    tx = stores["transmissions"].start(project_id=PROJ_A,
                                       approval=perms.get("appr-i2d-tx"),
                                       scope=tx_scope)
    assert tx.status == "started"
    done = stores["transmissions"].finish(tx.id, response_text="fixture reply",
                                          input_tokens=5, output_tokens=7)
    assert done.id == tx.id
    assert done.status == "succeeded"

    prof = stores["profiles"].create(
        {"provider_kind": "openai_compatible", "label": "Alpha local",
         "model": "ministral-3-14b",
         "endpoint": "https://models.example.com/v1",
         "pricing_basis": "token", "input_cost_per_million": 0.1,
         "output_cost_per_million": 0.2, "price_per_request_usd": 0})
    seeded["profile_a"] = prof.profile_id
    hub_prov, cred_ref = stores["hub"].create_provider(
        {"provider_kind": "openai_compatible", "name": "Alpha provider",
         "endpoint": "https://api.example.com/v1",
         "model_ids": ["ministral-3-14b"], "pricing_mode": "auto"})
    seeded["hub_provider_a"] = hub_prov["id"]
    assert cred_ref.startswith("model-hub.")
    team = stores["hub"].save_team(
        {"name": "Alpha team", "description": "I2D fixture team",
         "model_refs": ["ministral-3-14b"], "quality_policy": "strict",
         "default_budget": 5.0, "fallback_permissions": {}},
        team_id="team-i2d-a")
    seeded["hub_team_a"] = team["id"]

    fingerprint = compute_key_fingerprint("fixture-opaque-key-material-0001")
    stores["credgov"].create_credential(
        credential_id="cred-i2d-a", workspace_id=WS_A,
        provider_name="openai_compatible", key_fingerprint=fingerprint)

    cog = stores["cogworkflows"]
    cog.save_workflow(CognitiveWorkflowRecord(
        workflow_id="wf-i2d-a", workspace_id=WS_A, state="draft",
        goal=None, plan=None, selection=None, knowledge=None,
        verification=None, learning=None, failure_reason=None,
        created_at=TS, updated_at=TS))
    cog.save_workflow(CognitiveWorkflowRecord(
        workflow_id="wf-i2d-b", workspace_id=WS_B, state="draft",
        goal=None, plan=None, selection=None, knowledge=None,
        verification=None, learning=None, failure_reason=None,
        created_at=TS, updated_at=TS))

    rt = stores["runtime"]
    rt.save_session(RuntimeSessionRecord(
        session_id="sess-i2d-a", workflow_id="wf-i2d-a",
        workspace_id=WS_A, execution_mode="solo",
        selected_models=("ministral-3-14b",),
        participating_agents=("agent-1",),
        governance_check_passed=True,
        governance_status_snapshot={"check": "pass"},
        status="INITIALIZED", artifacts_produced=(),
        error_message=None, started_at=TS, finished_at=None))
    rt.record_evidence(RuntimeEvidenceRecord(
        evidence_id="ev-i2d-a", session_id="sess-i2d-a",
        workflow_id="wf-i2d-a", workspace_id=WS_A, step_index=0,
        event_type="session_start", recorded_facts={"ok": True},
        timestamp=TS))

    obs = stores["observability"]
    obs.append_event(RuntimeEventStreamItem(
        event_id="ose-i2d-a", session_id="sess-i2d-a",
        workflow_id="wf-i2d-a", workspace_id=WS_A, seq_num=0,
        event_type="session_start", payload={"ok": True}, timestamp=TS))
    obs.record_failure(RuntimeFailureRecord(
        failure_id="osf-i2d-a", session_id="sess-i2d-a",
        workflow_id="wf-i2d-a", workspace_id=WS_A,
        failed_component="probe-worker", error_code="E_PROBE",
        error_message="Simulated historical failure.",
        error_metadata={"simulated": True}, timestamp=TS))
    obs.set_recovery_state(session_id="sess-i2d-a", workspace_id=WS_A,
                           recovery_state="RECOVERED", reason="i2d fixture")

    wfopt = stores["workflowopt"]
    wfopt.save_analysis(WorkflowAnalysisRecord(
        analysis_id="an-i2d-a", project_id=PROJ_A,
        workflow_id="wf-i2d-a", total_duration_seconds=12.5,
        step_count=3, failure_count=0, local_execution_ratio=1.0,
        bottleneck_step="step-2",
        recommendations=(OptimizationRecommendation(
            recommendation_id="rec-i2d-a", category="latency",
            summary="Cache repeated retrieval.",
            evidence="step-2 dominates runtime.",
            suggested_action="Enable retrieval cache.",
            potential_savings_usd=1.25),),
        created_at=TS))

    gov = stores["workspacegov"]
    gov.log_action(workspace_id=WS_A, actor_id="op-i2d", role="admin",
                   action="fixture_seed", resource_type="workspace",
                   resource_id=WS_A)
    return seeded


def _build_i2d_fixture(tmp_path: Path) -> dict:
    """Create legacy v0.9 DB, snapshot it, run the upgrade, seed, snapshot."""
    db_path = tmp_path / "v090_canonical.db"
    payloads = tmp_path / "payloads"
    _create_v090_legacy_db(db_path)
    before = _dump_db(db_path)
    stores = _open_all_stores(db_path, payloads)
    try:
        seeded = _seed_current_shape_records(stores)
    finally:
        _close_all_stores(stores)
    after = _dump_db(db_path)
    manifest = {
        "fixture": "OPENCODE-PS2-I2D",
        "app_version": APP_VERSION_090,
        "upgrade_target": "v1.0.0 candidate schema (current code)",
        "workspaces": [WS_A, WS_B],
        "projects": [PROJ_A, PROJ_B],
    }
    (tmp_path / "fixture_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return {"db": db_path, "payloads": payloads, "root": tmp_path,
            "before": before, "after": after, "seeded": seeded,
            "manifest": manifest}


def _row_by_pk(rows: list[dict], pk: str, value: object) -> dict:
    for row in rows:
        if row.get(pk) == value:
            return row
    raise AssertionError(f"row with {pk}={value!r} not found")


# --------------------------------------------------------------------------
# 1. Baseline + preservation
# --------------------------------------------------------------------------

def test_fixture_establishes_v090_baseline(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    for table in LEGACY_TABLES:
        assert table in fx["before"], f"legacy table {table} missing"
        assert len(fx["before"][table]) >= 1, f"legacy table {table} is empty"
    con = sqlite3.connect(str(fx["db"]))
    try:
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
    finally:
        con.close()
    assert version == SQLiteStore.SCHEMA_VERSION == 1
    assert fx["manifest"]["app_version"] == "0.9.0"


def test_upgrade_preserves_all_legacy_records(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    before, after = fx["before"], fx["after"]
    # (before_table, after_table, pk, ignored_after_only_columns)
    # I2D.1 repaired: the pre-C1 conversations shape now migrates, so its
    # rows are compared like every other legacy table.
    cases = [
        ("research_runs", "research_runs", "run_id", set()),
        ("research_run_steps", "research_run_steps", "step_id", set()),
        ("studio_project_memories", "studio_project_memories", "memory_id", set()),
        ("desktop_conversations", "desktop_conversations", "conversation_id", set()),
        ("desktop_conversation_messages", "desktop_conversation_messages",
         "message_id", set()),
        ("desktop_automations", "desktop_automations", "id", set()),
        ("desktop_agent_organizations", "desktop_agent_organizations",
         "organization_id", set()),
        ("desktop_agent_tasks", "desktop_agent_tasks", "id", set()),
        ("desktop_agent_call_attempts", "desktop_agent_call_attempts",
         "call_attempt_id", set()),
        ("desktop_agent_reservations", "desktop_agent_reservations",
         "reservation_id", set()),
        ("research_planning_turns", "research_planning_turns", "turn_id", set()),
        ("research_planning_events", "research_planning_events", "event_id", set()),
        ("research_retrieval_metrics", "research_retrieval_metrics",
         "project_id", set()),
    ]
    for before_table, after_table, pk, _ in cases:
        assert after_table in after
        for brow in before[before_table]:
            arow = _row_by_pk(after[after_table], pk, brow[pk])
            for key, value in brow.items():
                assert arow.get(key) == value, (
                    f"{after_table}.{brow[pk]}.{key} changed by migration: "
                    f"{value!r} -> {arow.get(key)!r}")
    # Verdicts: dev-era table is renamed, legacy rows preserved verbatim.
    legacy_verdicts = before["desktop_agent_verification_verdicts"]
    preserved = after["desktop_agent_verification_verdicts_v1_legacy"]
    assert len(preserved) == len(legacy_verdicts) == 1
    for key, value in legacy_verdicts[0].items():
        assert preserved[0][key] == value
    assert after["desktop_agent_verification_verdicts"] == []
    # Interrupted automation claim is reconciled, never deleted.
    occ = _row_by_pk(after["desktop_automation_occurrences"], "id", "occ-legacy-a")
    assert occ["status"] == "failed"
    assert occ["error"]
    assert occ["completed_at"]


def test_no_destructive_table_loss(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    assert set(fx["after"]) >= set(fx["before"])
    for table in LEGACY_TABLES:
        if table == "desktop_agent_verification_verdicts":
            continue  # renamed by design; covered above
        if table == "desktop_automation_occurrences":
            continue  # rows preserved; status reconciled by design
        before_ids = {json.dumps(r, sort_keys=True, default=str)
                      for r in fx["before"][table]}
        after_ids = {json.dumps(r, sort_keys=True, default=str)
                     for r in fx["after"][table]}
        # CREATE-only stores only add rows; migrated tables keep every row
        # (automation occurrences keep the same row count).
        assert len(fx["after"][table]) >= len(before_ids)
        for digest in before_ids:
            assert digest in after_ids or table in (
                "research_runs", "research_run_steps",
                "studio_project_memories", "desktop_agent_tasks",
                "desktop_agent_call_attempts", "research_planning_turns",
                "desktop_automations", "desktop_conversations",
            ), f"legacy row lost from {table}"


def test_migration_adds_expected_columns_with_safe_defaults(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    db = fx["db"]
    cols = _table_columns(db, "desktop_agent_tasks")
    assert {"max_rounds", "max_adaptive_phases", "sync_mode"} <= set(cols)
    task = _row_by_pk(
        _dump_table(db, "desktop_agent_tasks"), "id", "task-legacy-a")
    assert task["max_rounds"] == 1
    assert task["max_adaptive_phases"] == 4
    assert task["focus"] == "Legacy alpha focus"
    call_cols = _table_columns(db, "desktop_agent_call_attempts")
    assert {"input_tokens", "output_tokens", "token_source",
            "batch_transport_id", "provider_latency_ms"} <= set(call_cols)
    mem = _row_by_pk(
        _dump_table(db, "studio_project_memories"), "memory_id", "mem-legacy-a")
    assert mem["confidence"] == 1.0
    assert mem["classification"] == "factual"
    assert mem["content"] == "The alpha workspace prefers local models."
    # I2D.1 repaired: the ordered migration adds the column before the
    # rebuild, so it is present on upgraded pre-C1 databases.
    conv_cols = _table_columns(db, "desktop_conversations")
    assert "workspace_project_id" in conv_cols
    legacy_conv = _row_by_pk(
        _dump_table(db, "desktop_conversations"), "conversation_id",
        "conv-legacy-a")
    assert legacy_conv["mode"] == "joint"
    assert legacy_conv["workspace_project_id"] is None
    run = _row_by_pk(_dump_table(db, "research_runs"), "run_id", "run-legacy-a")
    assert run["objective"] == "Legacy alpha objective"
    auto = _row_by_pk(_dump_table(db, "desktop_automations"), "id",
                      "auto-legacy-a")
    assert auto["revision"] == 0
    assert auto["goal"] == "Nightly alpha digest"


def test_repeated_migration_execution_is_safe(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    db, payloads = fx["db"], fx["payloads"]
    first = _dump_db(db)
    stores = _open_all_stores(db, payloads)
    try:
        core = stores["core"]
        # Re-saving the identical snapshot object must be a silent no-op
        # (immutable-snapshot protection rejects only DIVERGENT content).
        identical = core.load_state(PROJ_A)
        assert identical is not None
        core.save_state(identical)
        occ = stores["automations"].claim_occurrence("auto-i2d-a", TS)
        assert occ is None or occ["status"] == "claimed"
        # Re-claiming the same occurrence must not duplicate it.
        again = stores["automations"].claim_occurrence("auto-i2d-a", TS)
        assert again is None
    finally:
        _close_all_stores(stores)
    second = _dump_db(db)
    assert set(second) == set(first)
    for table, rows in first.items():
        if table == "desktop_automation_occurrences":
            # This test claims one new occurrence; exactly one row is added.
            assert len(second[table]) == len(rows) + 1
            continue
        assert len(second[table]) == len(rows), (
            f"repeated upgrade changed row count in {table}")
    occ_rows = [r for r in second["desktop_automation_occurrences"]
                 if r["automation_id"] == "auto-i2d-a"
                 and r["scheduled_for"] == TS]
    assert len(occ_rows) <= 1


def test_ids_and_references_preserved(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    db = fx["db"]
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        msg = con.execute(
            "SELECT * FROM desktop_conversation_messages "
            "WHERE message_id = 'msg-legacy-a'").fetchone()
        parent = con.execute(
            "SELECT * FROM desktop_conversations WHERE conversation_id = ?",
            (msg["conversation_id"],)).fetchone()
        assert parent is not None and parent["conversation_id"] == "conv-legacy-a"
        step = con.execute(
            "SELECT * FROM research_run_steps WHERE step_id = 'step-legacy-a'"
        ).fetchone()
        run = con.execute(
            "SELECT * FROM research_runs WHERE run_id = ?",
            (step["run_id"],)).fetchone()
        assert run is not None and run["run_id"] == "run-legacy-a"
        call = con.execute(
            "SELECT * FROM desktop_agent_call_attempts "
            "WHERE call_attempt_id = 'call-legacy-a'").fetchone()
        task = con.execute(
            "SELECT * FROM desktop_agent_tasks WHERE id = ?",
            (call["task_id"],)).fetchone()
        org = con.execute(
            "SELECT * FROM desktop_agent_organizations WHERE organization_id = ?",
            (task["organization_id"],)).fetchone()
        assert task["id"] == "task-legacy-a"
        assert org["organization_id"] == "org-legacy-a"
    finally:
        con.close()
    stores = _open_all_stores(db, fx["payloads"])
    try:
        art = stores["artifacts"]
        aid = fx["seeded"]["artifact_a"]
        versions = art.get(aid)["versions"]
        assert [v["version"] for v in versions] == [1, 2]
        assert versions[0]["payload_sha256"] != versions[1]["payload_sha256"]
        payload = art.read_source(aid, version=1)
        assert "Alpha v0.9 design" in payload["content"]
        assert payload["provenance"]["operation"] == "author"
    finally:
        _close_all_stores(stores)


def test_seeded_current_shape_records_readable_after_upgrade(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        legacy_run = stores["runs"].get_run("run-legacy-a")
        assert legacy_run is not None
        assert legacy_run["mode"] == "deterministic"  # backfilled default
        assert legacy_run["objective"] == "Legacy alpha objective"
        legacy_conv = stores["conversations"].get("conv-legacy-a")
        assert legacy_conv is not None
        assert legacy_conv["conversation"]["mode"] == "joint"
        assert legacy_conv["conversation"]["title"] == "Legacy chat"
        assert legacy_conv["conversation"]["workspace_project_id"] is None
        new_conv = stores["conversations"].get("conv-i2d-a")
        assert new_conv is not None
        assert new_conv["conversation"]["mode"] == "adaptive_hybrid"
        assert new_conv["conversation"]["workspace_project_id"] == WS_A
        assert stores["research"].get_session(
            fx["seeded"]["research_session_a"]) is not None
        assert stores["synthesis"].get_claim(
            fx["seeded"]["claim_a"]) is not None
        assert stores["context"].get_package("ctx-i2d-a") is not None
        team_ids = {t["id"] for t in stores["hub"].list_teams()}
        assert "team-i2d-a" in team_ids
        provider_ids = {p["id"] for p in stores["hub"].list_providers()}
        assert fx["seeded"]["hub_provider_a"] in provider_ids
        profile = stores["profiles"].get(fx["seeded"]["profile_a"])
        assert profile is not None and profile.provider_kind == "openai_compatible"
        credential = stores["credgov"].get_credential(
            credential_id="cred-i2d-a", workspace_id=WS_A)
        assert credential is not None and credential.state == "ACTIVE"
        assert credential.key_fingerprint.startswith("sha256:")
        assert stores["permissions"].get("appr-i2d-a") is not None
        events = stores["observability"].list_events(
            session_id="sess-i2d-a", workspace_id=WS_A)
        assert [e.event_id for e in events] == ["ose-i2d-a"]
        failures = stores["observability"].list_failures(
            session_id="sess-i2d-a", workspace_id=WS_A)
        assert [f.failure_id for f in failures] == ["osf-i2d-a"]
        assert stores["observability"].get_recovery_state(
            session_id="sess-i2d-a", workspace_id=WS_A) == "RECOVERED"
        analyses = stores["workflowopt"].list_analyses(PROJ_A)
        assert [a["analysis_id"] for a in analyses] == ["an-i2d-a"]
        audits = stores["workspacegov"].list_audit_records(WS_A)
        assert len(audits) == 1 and audits[0]["resource_id"] == WS_A
        legacy_auto = stores["automations"].get("auto-legacy-a")
        assert legacy_auto is not None and legacy_auto["revision"] == 0
        legacy_task = stores["recovery"].get_task("task-legacy-a")
        assert legacy_task is not None
        assert legacy_task["focus"] == "Legacy alpha focus"
    finally:
        _close_all_stores(stores)


def test_data_integrity_checksums(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        core = stores["core"]
        state = core.load_state(PROJ_A)
        assert state is not None
        recomputed = hashlib.sha256(state.to_json().encode("utf-8")).hexdigest()
        export = build_audit_export(core, PROJ_A)
        assert export["latest_state"]["sha256"] == recomputed
        assert export["audit"]["event_digest_mismatch_count"] == 0
        assert export["audit"]["latest_state_sha256"] == recomputed
        art = stores["artifacts"]
        aid = fx["seeded"]["artifact_a"]
        payload_dir = fx["payloads"] / aid
        for version in (1, 2):
            record = art.read_source(aid, version=version)
            disk = (payload_dir / f"v{version}.md").read_bytes()
            assert record["content"].encode("utf-8") == disk
            assert hashlib.sha256(disk).hexdigest() == record["provenance"]["sha256"]
            assert record["provenance"]["bytes"] == len(disk)
    finally:
        _close_all_stores(stores)


# --------------------------------------------------------------------------
# 2. Backup -> upgrade -> restore
# --------------------------------------------------------------------------

def _workspace_payload(stores: dict, workspace_id: str, project_id: str):
    artifacts = [
        {"id": a["artifact_id"], "title": a["title"],
         "current_version": a["current_version"]}
        for a in stores["artifacts"].list_for_project(project_id)
    ]
    knowledge = stores["knowledge"].list_assets(workspace_id)
    return (
        [{"id": a["asset_id"], "title": a["title"]} for a in knowledge],
        artifacts,
    )


def test_backup_export_integrity(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        knowledge, artifacts = _workspace_payload(stores, WS_A, PROJ_A)
        out = tmp_path / "backups" / "ws-alpha.zip"
        res = BackupRestoreManager.export_workspace_backup(
            workspace_id=WS_A, workspace_name="Alpha",
            artifacts=artifacts, knowledge_records=knowledge,
            output_zip=out)
    finally:
        _close_all_stores(stores)
    assert out.is_file()
    assert res["manifest"]["format_version"] == "1.0.0"
    assert res["manifest"]["workspace_id"] == WS_A
    assert res["manifest"]["artifact_count"] == len(artifacts) >= 1
    assert res["manifest"]["knowledge_record_count"] == len(knowledge) >= 1
    assert res["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    with zipfile.ZipFile(out) as zf:
        assert set(zf.namelist()) == {"manifest.json", "artifacts.json",
                                      "knowledge.json"}


def test_restore_to_empty_target_is_consistent(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        knowledge, artifacts = _workspace_payload(stores, WS_A, PROJ_A)
        backup = tmp_path / "ws-alpha.zip"
        BackupRestoreManager.export_workspace_backup(
            workspace_id=WS_A, workspace_name="Alpha",
            artifacts=artifacts, knowledge_records=knowledge,
            output_zip=backup)
    finally:
        _close_all_stores(stores)
    target = tmp_path / "restored-alpha"
    res = BackupRestoreManager.validate_and_restore_backup(
        backup_zip=backup, target_dir=target)
    assert res["restored"] is True
    assert res["workspace_id"] == WS_A
    assert res["artifacts_restored"] == len(artifacts)
    assert res["knowledge_records_restored"] == len(knowledge)
    marker = json.loads((target / "workspace_state.json").read_text(
        encoding="utf-8"))
    assert marker["workspace_id"] == WS_A
    restored_artifacts = json.loads((target / "artifacts_restored.json").read_text(
        encoding="utf-8"))
    assert restored_artifacts == artifacts


def test_default_restore_is_non_destructive(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    backup = tmp_path / "ws-alpha.zip"
    BackupRestoreManager.export_workspace_backup(
        workspace_id=WS_A, workspace_name="Alpha", artifacts=[{"id": "a1"}],
        knowledge_records=[], output_zip=backup)
    target = tmp_path / "occupied"
    target.mkdir(parents=True)
    marker = target / "workspace_state.json"
    marker.write_text('{"workspace_id": "ws-i2d-beta"}', encoding="utf-8")
    refused = BackupRestoreManager.validate_and_restore_backup(
        backup_zip=backup, target_dir=target,
        allow_destructive_overwrite=False)
    assert refused["restored"] is False
    assert refused["reason"] == "TARGET_EXISTS_OVERWRITE_FORBIDDEN"
    assert json.loads(marker.read_text(encoding="utf-8"))["workspace_id"] \
        == "ws-i2d-beta"


def test_explicit_overwrite_restore(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    backup = tmp_path / "ws-alpha.zip"
    BackupRestoreManager.export_workspace_backup(
        workspace_id=WS_A, workspace_name="Alpha", artifacts=[{"id": "a1"}],
        knowledge_records=[], output_zip=backup)
    target = tmp_path / "occupied"
    target.mkdir(parents=True)
    (target / "workspace_state.json").write_text("{}", encoding="utf-8")
    allowed = BackupRestoreManager.validate_and_restore_backup(
        backup_zip=backup, target_dir=target,
        allow_destructive_overwrite=True)
    assert allowed["restored"] is True
    assert allowed["workspace_id"] == WS_A
    assert allowed["artifacts_restored"] == 1


def test_corrupt_and_tampered_backups_rejected(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    backup = tmp_path / "ws-alpha.zip"
    res = BackupRestoreManager.export_workspace_backup(
        workspace_id=WS_A, workspace_name="Alpha",
        artifacts=[{"id": "a1", "title": "Alpha"}],
        knowledge_records=[{"id": "k1", "title": "Know"}],
        output_zip=backup)
    recorded_sha = res["sha256"]
    with pytest.raises(FileNotFoundError):
        BackupRestoreManager.validate_and_restore_backup(
            backup_zip=tmp_path / "missing.zip",
            target_dir=tmp_path / "t1")
    no_manifest = tmp_path / "no-manifest.zip"
    with zipfile.ZipFile(no_manifest, "w") as zf:
        zf.writestr("artifacts.json", "[]")
    with pytest.raises(ValueError, match="manifest"):
        BackupRestoreManager.validate_and_restore_backup(
            backup_zip=no_manifest, target_dir=tmp_path / "t2")
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(backup.read_bytes()[:64])
    with pytest.raises(zipfile.BadZipFile):
        BackupRestoreManager.validate_and_restore_backup(
            backup_zip=truncated, target_dir=tmp_path / "t3")
    # Tampered payload: restoring still reflects the bytes on disk, but the
    # export-time SHA no longer matches, so tampering is detectable.
    # (Rewritten cleanly to avoid duplicate zip members.)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(backup) as src_zf, \
            zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as dst_zf:
        for name in src_zf.namelist():
            data = src_zf.read(name)
            if name == "artifacts.json":
                data = b'[{"id": "evil", "title": "Forged"}]'
            dst_zf.writestr(name, data)
    assert hashlib.sha256(tampered.read_bytes()).hexdigest() != recorded_sha
    tres = BackupRestoreManager.validate_and_restore_backup(
        backup_zip=tampered, target_dir=tmp_path / "t4")
    assert tres["restored"] is True
    assert tres["artifacts_restored"] == 1  # reflects tampered bytes as-is


def test_database_file_backup_and_reopen(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    before = _dump_db(fx["db"])
    copy_path = tmp_path / "restored_canonical.db"
    src = sqlite3.connect(str(fx["db"]))
    try:
        dst = sqlite3.connect(str(copy_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    stores = _open_all_stores(copy_path, tmp_path / "payloads-copy")
    try:
        assert stores["core"].load_state(PROJ_A) is not None
        assert stores["core"].load_state(PROJ_B) is not None
        assert len(stores["memory"].list_memories(PROJ_A)) >= 2
    finally:
        _close_all_stores(stores)
    after = _dump_db(copy_path)
    assert set(after) == set(before)
    for table in before:
        assert len(after[table]) == len(before[table])


def test_artifact_version_restore(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        art = stores["artifacts"]
        aid = fx["seeded"]["artifact_a"]
        v1 = art.read_source(aid, version=1)
        restored = art.restore(artifact_id=aid, version=1, created_by="op-i2d")
        assert restored["current_version"] == 3
        v3 = art.read_source(aid, version=3)
        assert v3["content"] == v1["content"]
        assert v3["provenance"]["sha256"] == v1["provenance"]["sha256"]
        assert v3["provenance"]["restored_from_version"] == 1
        history = art.get(aid)["versions"]
        assert history[-1]["operation"] == "restore"
        assert json.loads(history[-1]["provenance"])["restored_from_version"] == 1
        with pytest.raises(ArtifactError):
            art.edit(artifact_id=aid, content="stale write",
                     created_by="op-i2d", base_version=1)
    finally:
        _close_all_stores(stores)


def test_state_snapshot_restore(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        core = stores["core"]
        latest = StateManager.restore(core, PROJ_A).snapshot()
        assert latest.project_id == PROJ_A
        assert latest.goal == "Alpha workspace goal"
        pinned = StateManager.restore(core, PROJ_A, version=0).snapshot()
        assert pinned.goal == "Alpha workspace goal"
        with pytest.raises(KeyError):
            StateManager.restore(core, "proj-does-not-exist")
        with pytest.raises(KeyError):
            StateManager.restore(core, PROJ_A, version=99)
    finally:
        _close_all_stores(stores)


# --------------------------------------------------------------------------
# 3. Failure honesty
# --------------------------------------------------------------------------

def test_failed_migration_safety(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    future = tmp_path / "future.db"
    con = sqlite3.connect(str(future))
    try:
        con.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
        con.execute("PRAGMA user_version = 2")
        con.commit()
    finally:
        con.close()
    with pytest.raises(RuntimeError, match="unsupported AMCA"):
        SQLiteStore(future)
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"\x00\x01not-a-database" * 64)
    size_before = garbage.stat().st_size
    with pytest.raises(Exception, match="(?i)database|file|unsupported|malformed"):
        SQLiteStore(garbage)
    assert garbage.stat().st_size == size_before  # never deletes input
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        with pytest.raises(ValueError):
            stores["conversations"].create(title="Bad mode", mode="nope")
        with pytest.raises(ValueError):
            stores["conversations"].add_message(
                "conv-missing", message_id="m-x", role="user",
                content="hello")
        with pytest.raises(Exception):
            stores["artifacts"].get("art-missing")
    finally:
        _close_all_stores(stores)


def test_interrupted_migration_recovery(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    occ = _row_by_pk(fx["after"]["desktop_automation_occurrences"], "id",
                     "occ-legacy-a")
    assert occ["status"] == "failed"
    assert occ["run_id"] is None
    assert "interrupt" in occ["error"].lower() or "claim" in occ["error"].lower()
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        auto = stores["automations"]
        record = auto.get_occurrence("occ-legacy-a")
        assert record is not None and record["status"] == "failed"
        claimed = [r for r in auto.list_occurrences("auto-legacy-a")
                   if r["id"] == "occ-legacy-a"]
        assert len(claimed) == 1  # reconciled, never duplicated
    finally:
        _close_all_stores(stores)


def test_pre_c1_conversations_shape_migration(tmp_path: Path):
    """I2D-001: a faithful pre-C1 v0.9 database must upgrade its chat history.

    History: this test demonstrated production defect I2D-001
    (ConversationStore._migrate_conversation_modes ran before the
    workspace_project_id ALTER, crashing on pre-C1 shapes with
    OperationalError). Repaired under I2D.1 by ordering the ALTER before the
    rebuild in ConversationStore.__init__ (software/src/amca/chat_history.py).
    Retained as the regression test: it now asserts the repaired end state.
    """
    db_path = tmp_path / "v090_canonical.db"
    _create_v090_legacy_db(db_path)
    store = ConversationStore(db_path)  # <-- raises OperationalError (I2D-001)
    try:
        legacy = store.get("conv-legacy-a")
        assert legacy is not None
        assert legacy["conversation"]["mode"] == "joint"
        assert legacy["conversation"]["title"] == "Legacy chat"
        beta = store.get("conv-legacy-b")
        assert beta is not None and beta["conversation"]["mode"] == "solo"
        widended = store.create(
            title="Post-upgrade chat", mode="adaptive_hybrid",
            participant_ids=["agent-1"], conversation_id="conv-i2d-new",
            workspace_project_id=WS_A)
        assert widended["mode"] == "adaptive_hybrid"
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()


def test_conversations_migration_variants(tmp_path: Path):
    """I2D.1 required coverage: modern shape untouched, repair idempotent."""
    db_path = tmp_path / "modern.db"
    first = ConversationStore(db_path)
    try:
        first.create(title="Modern chat", mode="parallel",
                     participant_ids=["agent-1", "agent-2"],
                     conversation_id="conv-modern-a",
                     workspace_project_id=WS_A)
        first.add_message("conv-modern-a", message_id="msg-modern-a",
                          role="assistant", content="Modern reply.",
                          project_id=PROJ_A)
    finally:
        close = getattr(first, "close", None)
        if callable(close):
            close()
    before = _dump_db(db_path)
    cols_before = _table_columns(db_path, "desktop_conversations")
    assert cols_before.count("workspace_project_id") == 1
    # Reopen twice: current-schema DB must be unchanged, no duplicate
    # columns, no record-count loss, values (incl. workspace) preserved.
    for _ in range(2):
        store = ConversationStore(db_path)
        try:
            doc = store.get("conv-modern-a")
            assert doc is not None
            assert doc["conversation"]["mode"] == "parallel"
            assert doc["conversation"]["workspace_project_id"] == WS_A
            assert doc["conversation"]["project_id"] is None
            stored = store.add_message(
                "conv-modern-a", message_id="msg-modern-a", role="assistant",
                content="Modern reply.", project_id=PROJ_A)
            assert stored["message_id"] == "msg-modern-a"  # idempotent replay
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()
    after = _dump_db(db_path)
    assert set(after) == set(before)
    for table, rows in before.items():
        assert len(after[table]) == len(rows)
    assert _table_columns(db_path, "desktop_conversations") == cols_before
    modern = _row_by_pk(after["desktop_conversations"], "conversation_id",
                        "conv-modern-a")
    assert modern["workspace_project_id"] == WS_A
    assert modern["participant_ids_json"] == '["agent-1","agent-2"]'


# --------------------------------------------------------------------------
# 4. Workspace isolation
# --------------------------------------------------------------------------

def test_workspace_isolation(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        mem_a = {m["memory_id"] for m in stores["memory"].list_memories(PROJ_A)}
        mem_b = {m["memory_id"] for m in stores["memory"].list_memories(PROJ_B)}
        assert "mem-legacy-a" in mem_a and "mem-legacy-b" in mem_b
        assert mem_a.isdisjoint(mem_b)
        assert {a["asset_id"] for a in
                stores["knowledge"].list_assets(WS_A)} == {"k-i2d-a"}
        assert {a["asset_id"] for a in
                stores["knowledge"].list_assets(WS_B)} == {"k-i2d-b"}
        assert stores["vectors"].get_vector(
            artifact_id="k-i2d-a", workspace_id=WS_B) is None
        assert stores["vectors"].get_vector(
            artifact_id="k-i2d-a", workspace_id=WS_A) is not None
        hits_b = stores["fts"].search(workspace_id=WS_B, query="reusable")
        assert [h.artifact_id for h in hits_b] == []
        hits_a = stores["fts"].search(workspace_id=WS_A, query="reusable")
        assert [h.artifact_id for h in hits_a] == ["k-i2d-a"]
        assert {e.event_id for e in
                stores["audit"].list_events(workspace_id=WS_A)} == {f"kae-i2d-{WS_A[-5:]}"}
        assert {a["artifact_id"] for a in
                stores["artifacts"].list_for_project(PROJ_A)} == {
                    fx["seeded"]["artifact_a"]}
        assert {a["artifact_id"] for a in
                stores["artifacts"].list_for_project(PROJ_B)} == {
                    fx["seeded"]["artifact_b"]}
        assert stores["cogworkflows"].get_workflow(
            workflow_id="wf-i2d-a", workspace_id=WS_B) is None
        assert stores["cogworkflows"].get_workflow(
            workflow_id="wf-i2d-a", workspace_id=WS_A) is not None
        assert stores["runtime"].get_session(
            session_id="sess-i2d-a", workspace_id=WS_B) is None
        assert stores["runtime"].get_session(
            session_id="sess-i2d-a", workspace_id=WS_A) is not None
        assert stores["permissions"].list(project_id=PROJ_B) == ()
        assert len(stores["permissions"].list(project_id=PROJ_A)) == 2
    finally:
        _close_all_stores(stores)
    con = sqlite3.connect(str(fx["db"]))
    try:
        cross = con.execute(
            "SELECT COUNT(*) FROM studio_project_memories WHERE project_id = ? "
            "AND memory_id LIKE 'mem-legacy-b'", (PROJ_A,)).fetchone()[0]
        assert cross == 0
    finally:
        con.close()


# --------------------------------------------------------------------------
# 5. Secrets, provenance, version honesty
# --------------------------------------------------------------------------

def test_no_secret_material_introduced(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        with pytest.raises(ValueError):
            stores["memory"].save_memory(
                project_id=PROJ_A, title="Bad",
                content="token sk-proj-fake123 in text")
        with pytest.raises(ValueError):
            stores["credgov"].create_credential(
                credential_id="cred-raw", workspace_id=WS_A,
                provider_name="openai_compatible",
                key_fingerprint="sk-fake-raw-secret-0123456789abcdef")
        with pytest.raises(ValueError):
            BenchmarkEvidenceRegistry().register_evaluation(
                model_id="m", provider="local",
                task_category=EvaluationTaskCategory.CODING,
                dataset_reference="ds",
                environment=EvaluationExecutionEnvironment(
                    locality="local", hardware_spec="cpu",
                    runtime_daemon="none", endpoint_type="local"),
                measured_metrics={"rank": 1},
                raw_output_artifact_ref="art@1")
        with pytest.raises(Exception):
            stores["permissions"].request(
                project_id=PROJ_A, action="read_asset",
                scope={"api_key": "sk-fake"}, reason="bad scope")
        redacted = redact_secrets({"api_key": "sk-fake",
                                     "nested": {"access_token": "abc"}})
        assert redacted == {"api_key": "[REDACTED]",
                            "nested": {"access_token": "[REDACTED]"}}
        export = build_audit_export(stores["core"], PROJ_A)
        blob = json.dumps(export, default=str)
        assert SECRET_CANARY not in blob
    finally:
        _close_all_stores(stores)
    dump_blob = json.dumps(fx["after"], default=str)
    assert SECRET_CANARY not in dump_blob
    assert "BEGIN PRIVATE KEY" not in dump_blob


def test_version_boundary_honesty(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    assert UpdateReadinessManager.compare_versions("0.9.0", "0.9.0") \
        == UpdateState.UP_TO_DATE
    assert UpdateReadinessManager.compare_versions("0.9.0", "1.0.0") \
        == UpdateState.UPDATE_AVAILABLE
    report = DiagnosticsManager().generate_diagnostic_report(providers_config=[])
    assert report.product_version == "0.9.0"
    assert report.telemetry_uploaded is False
    assert EXPORT_SCHEMA_VERSION == "1.0.0"
    assert SQLiteStore.SCHEMA_VERSION == 1
    manifest = json.loads((fx["root"] / "fixture_manifest.json").read_text(
        encoding="utf-8"))
    assert manifest["app_version"] == "0.9.0"
    stores = _open_all_stores(fx["db"], fx["payloads"])
    try:
        export = build_audit_export(stores["core"], PROJ_A)
        assert export["schema_version"] == "1.0.0"
        assert export["database_schema_version"] == 1
    finally:
        _close_all_stores(stores)


def test_dpapi_credential_schema_strictness(tmp_path: Path):
    store_target = tmp_path / "credentials.dpapi.json"
    store_target.write_text(json.dumps({"schema_version": 999, "entries": {}}),
                            encoding="utf-8")
    with pytest.raises(Exception, match="(?i)schema|unreadable|version|credential"):
        WindowsDpapiCredentialStore(store_target).get("any-provider")
    assert store_target.is_file()  # strict rejection never deletes user data


# --------------------------------------------------------------------------
# 6. Non-durable systems: evaluation / selection / binding continuity
# --------------------------------------------------------------------------

def test_in_memory_systems_recreatable_without_silent_fallback(tmp_path: Path):
    fx = _build_i2d_fixture(tmp_path)
    assert fx["seeded"]["claim_a"].startswith("clm-")

    def build_all():
        registry = BenchmarkEvidenceRegistry()
        env = EvaluationExecutionEnvironment(
            locality="local", hardware_spec="cpu", runtime_daemon="none",
            endpoint_type="local", approval_reference="OP-APP-I2D-01")
        record = registry.register_evaluation(
            model_id="ministral-3-14b", provider="local",
            task_category=EvaluationTaskCategory.CODING,
            dataset_reference="i2d-fixture-dataset", environment=env,
            measured_metrics={"pass_rate": 0.92, "tokens_per_sec": 48.2},
            raw_output_artifact_ref=f"{fx['seeded']['artifact_a']}@2",
            operator_verifier="op-i2d")
        selections = ModelSelectionManager()
        selection = selections.record_manual_selection(
            model_id="ministral-3-14b", project_context=PROJ_A,
            evidence_references_reviewed=[record.record_id],
            operator_rationale_notes="Local model preferred for offline alpha.",
            provider_approval_context="OP-APP-I2D-01")
        bindings = WorkflowModelBindingManager()
        bound = bindings.bind_selection_to_workflow(
            selection, workflow_id="wf-i2d-a", session_id="sess-i2d-a",
            model_locality="local", model_availability="available")
        blocked = bindings.bind_selection_to_workflow(
            selection, workflow_id="wf-i2d-b", session_id="sess-i2d-b",
            model_locality="cloud", model_availability="unavailable")
        return registry, selection, bound, blocked, bindings

    reg1, sel1, bound1, blocked1, mgr1 = build_all()
    reg2, sel2, bound2, blocked2, _mgr2 = build_all()
    assert sel1.selection_id == sel2.selection_id
    assert bound1.binding_id == bound2.binding_id
    assert reg1.get_record(sel1.selection_id) is None  # selections != evals
    assert str(bound1.binding_state) == \
        "WorkflowModelBindingState.MANUAL_OPERATOR_SELECTION"
    assert str(blocked1.binding_state) == \
        "WorkflowModelBindingState.BLOCKED_UNAVAILABLE"
    blocks = mgr1.list_blocks_for_workflow("wf-i2d-b")
    assert len(blocks) == 1
    assert blocks[0].silent_fallback_performed is False
    assert blocks[0].requires_explicit_user_action is True
    audit = getattr(mgr1, "audit_no_silent_fallback", None)
    if callable(audit):
        assert audit() in (True, None)
