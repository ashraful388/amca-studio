"""PS1-13H Agent Observability Core (backend core only).

Safe serialization, nano timing, truthful active count/organization,
lifecycle observability, cross-project isolation, 13C/13D preservation.

All mutable state under mandated scratch root
``D:\\ChatGPT\\AMCA\\.agent-tmp\\ps1-agent-13h``. Local-only, zero paid calls.
FAKE secret patterns only (never real credentials).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from amca.agent_fabric import AgentFabricStore
from amca.agent_observability import (
    ACTIVE_TASK_STATUSES,
    ALL_TASK_STATUSES,
    build_timing,
    count_active,
    describe_organization,
    is_task_active,
    lifecycle_counts,
    sanitize_output_schema,
    sanitize_resolved_model,
    sanitize_tool_config,
    summarize_input,
)
from amca.agent_runtime import (
    AgentTaskRecord,
    AgentTaskStatus,
    ExecutorKind,
    NanoScheduler,
    OrganizationRun,
)

MANDATED_SCRATCH = Path(r"D:\ChatGPT\AMCA\.agent-tmp\ps1-agent-13h")

# FAKE-ONLY secret canaries (never real material).
FAKE_BEARER = "Bearer TEST-SECRET-123"
FAKE_API_KEY = "api_key=FAKE_ONLY"
FAKE_PASSWORD = "password=NOT_REAL"

LEGACY_TASK_KEYS = {
    "id", "organization_id", "project_id", "agent_id", "focus", "status",
    "depends_on", "executor_kind", "verifier_ids", "participant_ids",
    "collaboration_architecture", "sync_mode", "max_rounds",
    "max_adaptive_phases", "decomposition_plan", "collaboration_payload",
    "collaboration_result", "requires_verified", "attempts", "result_summary",
    "verification_result", "error", "cost_usd", "local_calls", "api_calls",
    "reused_from", "reservation_id", "max_cost_usd", "proposed_children",
    "proposed_by_manager_id", "manager_depth", "proposal_reason",
    "proposal_version", "authorization_record", "focus_digest",
    "created_at", "started_at", "completed_at",
}


def _task(task_id="t-13h", project="p-13h", status=None, **kw):
    t = AgentTaskRecord(
        id=task_id, organization_id="o-13h", project_id=project,
        agent_id=kw.pop("agent_id", "a-13h"), focus=kw.pop("focus", "bounded focus"),
        **kw,
    )
    if status is not None:
        # Drive forward legally from PROPOSED.
        order = [
            AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
            AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
            AgentTaskStatus.WAITING, AgentTaskStatus.VERIFYING,
            AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED,
            AgentTaskStatus.RETIRED,
        ]
        # Map desired status to path.
        if status in order:
            for nxt in order:
                if t.status == status:
                    break
                try:
                    t.transition(nxt)
                except ValueError:
                    break
                if nxt == status:
                    break
        else:
            # Terminal branches: drive to RUNNING then branch.
            for nxt in (AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
                        AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING):
                t.transition(nxt)
            if status == AgentTaskStatus.FAILED:
                t.transition(AgentTaskStatus.FAILED)
            elif status == AgentTaskStatus.CANCELLED:
                t.transition(AgentTaskStatus.CANCELLED)
            elif status == AgentTaskStatus.VERIFYING:
                t.transition(AgentTaskStatus.VERIFYING)
    return t


class SerializationSafeTests(unittest.TestCase):
    def test_tool_config_recursive_sanitize_nested(self):
        cfg = {
            "tools": ["filesystem.read"],
            "auth": {"api_key": "FAKE_ONLY", "nested": {"password": "NOT_REAL"}},
            "headers": {"authorization": FAKE_BEARER},
            "deep": [{"token": "FAKE_ONLY", "ok": "keep"}],
            "private_key": "FAKE_ONLY",
            "credential": {"user": "u"},
            "cookie": "c",
            "bearer_token": "x",
            "secret": "s",
        }
        safe = sanitize_tool_config(cfg)
        blob = json.dumps(safe)
        self.assertNotIn("FAKE_ONLY", blob)
        self.assertNotIn("NOT_REAL", blob)
        self.assertNotIn("TEST-SECRET-123", blob)
        self.assertEqual(safe["tools"], ["filesystem.read"])
        self.assertEqual(safe["deep"][0]["ok"], "keep")
        # Original untouched.
        self.assertEqual(cfg["auth"]["api_key"], "FAKE_ONLY")

    def test_output_schema_as_is_if_safe(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        self.assertEqual(sanitize_output_schema(schema), schema)
        # Unsafe schema gets sanitized, never leaks.
        unsafe = {"type": "object", "api_key": "FAKE_ONLY"}
        safe = sanitize_output_schema(unsafe)
        self.assertNotIn("FAKE_ONLY", json.dumps(safe))

    def test_resolved_model_allowlist_only(self):
        cfg = {
            "model_id": "nano-local",
            "provider": "ollama",
            "is_local": True,
            "temperature": 0.2,
            "api_key": "FAKE_ONLY",
            "authorization": FAKE_BEARER,
            "headers": {"x": "y"},
            "credentials": "c",
            "secret": "s",
            "token": "t",
        }
        safe = sanitize_resolved_model(cfg)
        assert safe is not None
        self.assertEqual(safe.get("model_id"), "nano-local")
        self.assertEqual(safe.get("provider"), "ollama")
        self.assertIn("is_local", safe)
        blob = json.dumps(safe)
        self.assertNotIn("FAKE_ONLY", blob)
        self.assertNotIn("TEST-SECRET-123", blob)
        self.assertNotIn("headers", safe)
        self.assertNotIn("api_key", safe)
        self.assertNotIn("credentials", safe)

    def test_input_summary_no_raw_payload(self):
        payload = {"prompt": "hello", "api_key": "FAKE_ONLY", "nested": {"password": "NOT_REAL"}}
        summary = summarize_input(payload)
        assert summary is not None
        self.assertIn("digest", summary)
        self.assertIn("size_bytes", summary)
        self.assertIn("top_level_keys", summary)
        self.assertIn("preview", summary)
        blob = json.dumps(summary)
        self.assertNotIn("FAKE_ONLY", blob)
        self.assertNotIn("NOT_REAL", blob)
        # No raw payload key anywhere in task dict.
        t = _task()
        t.input_payload = dict(payload)
        d = t.to_dict()
        self.assertNotIn("input_payload", d)
        self.assertIn("input_summary", d)
        self.assertNotIn("FAKE_ONLY", json.dumps(d["input_summary"]))

    def test_task_to_dict_safe_additive_no_secret_material(self):
        t = _task("t-safe")
        t.tool_config = {"ok": "yes", "secret": "FAKE_ONLY", "nested": {"token": "FAKE_ONLY"}}
        t.output_schema = {"type": "object"}
        t.resolved_model_config = {"model_id": "m", "provider": "p", "is_local": True, "api_key": "FAKE_ONLY"}
        t.input_payload = {"q": "hi", "x": FAKE_BEARER}
        t.error = f"boom {FAKE_BEARER} {FAKE_API_KEY} {FAKE_PASSWORD}"
        d = t.to_dict()
        blob = json.dumps(d, default=str)
        self.assertNotIn("FAKE_ONLY", blob)
        self.assertNotIn("NOT_REAL", blob)
        self.assertNotIn("TEST-SECRET-123", blob)
        self.assertNotIn("input_payload", d)
        self.assertIn("tool_config", d)
        self.assertIn("output_schema", d)
        self.assertIn("resolved_model", d)
        self.assertIn("input_summary", d)
        self.assertIn("timing", d)


class BackwardCompatTests(unittest.TestCase):
    def test_existing_keys_semantics_preserved(self):
        t = _task("t-compat", status=AgentTaskStatus.RUNNING)
        d = t.to_dict()
        for key in LEGACY_TASK_KEYS:
            self.assertIn(key, d, key)
        self.assertEqual(d["id"], "t-compat")
        self.assertEqual(d["status"], AgentTaskStatus.RUNNING)
        # New fields additive.
        for key in ("tool_config", "output_schema", "resolved_model", "input_summary", "timing"):
            self.assertIn(key, d, key)

    def test_catalog_existing_keys_preserved(self):
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        try:
            store = AgentFabricStore(Path(tmp.name) / "s.sqlite3")
            c = store.catalog()
            for key in ("agents", "counts", "categories", "settings", "hierarchy", "status", "default_policy", "active_organization"):
                self.assertIn(key, c, key)
            for key in ("core", "built_in_specialists", "custom", "total_available", "active"):
                self.assertIn(key, c["counts"], key)
            self.assertIsNone(c["active_organization"])
        finally:
            tmp.cleanup()

    def test_org_to_dict_existing_keys_preserved(self):
        org = OrganizationRun(organization_id="o", project_id="p", goal="g", budget_usd=5.0, max_concurrent=2)
        d = org.to_dict()
        for key in ("organization_id", "project_id", "goal", "budget_usd", "max_concurrent",
                    "retired", "active_count", "completed_count", "incurred_cost_usd",
                    "total_cost_usd", "reserved_cost_usd", "available_budget_usd",
                    "tasks", "reservations"):
            self.assertIn(key, d, key)


class NanoTimingTests(unittest.TestCase):
    def test_timing_from_existing_lifecycle_missing_null(self):
        t = _task("t-timing")
        d = t.to_dict()
        timing = d["timing"]
        self.assertIsNotNone(timing["created_at"])
        self.assertIsNone(timing["started_at"])
        self.assertIsNone(timing["completed_at"])
        self.assertIsNone(timing["elapsed_seconds"])
        self.assertIsNone(timing["monotonic"])

    def test_timing_elapsed_and_monotonic(self):
        t = _task("t-full")
        t.started_at = "2026-09-09T00:00:01+00:00"
        t.created_at = "2026-09-09T00:00:00+00:00"
        t.completed_at = "2026-09-09T00:00:11+00:00"
        timing = build_timing(t.created_at, t.started_at, t.completed_at)
        self.assertEqual(timing["elapsed_seconds"], 11.0)
        self.assertTrue(timing["monotonic"])

    def test_timing_monotonic_violation_detected_no_fabrication(self):
        timing = build_timing(
            "2026-09-09T00:00:10+00:00",
            "2026-09-09T00:00:01+00:00",
            "2026-09-09T00:00:11+00:00",
        )
        self.assertFalse(timing["monotonic"])
        # Missing stays null, never fabricated.
        timing2 = build_timing(None, None, None)
        self.assertIsNone(timing2["created_at"])
        self.assertIsNone(timing2["elapsed_seconds"])
        self.assertIsNone(timing2["monotonic"])


class ActiveCountTests(unittest.TestCase):
    def test_authoritative_active_set(self):
        self.assertEqual(ACTIVE_TASK_STATUSES, frozenset({"queued", "starting", "running", "waiting", "verifying"}))
        org = OrganizationRun(organization_id="o", project_id="p", goal="g", budget_usd=5.0, max_concurrent=8)
        # One per every real state.
        for i, state in enumerate(ALL_TASK_STATUSES):
            tid = f"t-{state}"
            if state in ("proposed", "authorized"):
                t = AgentTaskRecord(id=tid, organization_id="o", project_id="p", agent_id="a", focus="f")
                if state == "authorized":
                    t.transition(AgentTaskStatus.AUTHORIZED)
            elif state in ("completed", "merged", "retired"):
                t = _task(tid, status=AgentTaskStatus.RETIRED)
                # Force exact desired terminal for counting.
                t.status = state
            elif state in ("failed", "cancelled"):
                t = _task(tid, status=state)
            else:
                t = _task(tid, status=state)
                # _task helper for waiting: drive RUNNING->WAITING manually.
                if state == "waiting" and t.status != "waiting":
                    # _task path doesn't cover WAITING; fix directly.
                    t2 = AgentTaskRecord(id=tid, organization_id="o", project_id="p", agent_id="a", focus="f")
                    for nxt in (AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
                                AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
                                AgentTaskStatus.WAITING):
                        t2.transition(nxt)
                    t = t2
            org.tasks[tid] = t
        self.assertEqual(org.active_count(), 5)
        d = org.to_dict()
        self.assertEqual(d["active_count"], 5)
        self.assertEqual(sum(d["lifecycle_counts"].values()), 12)

    def test_hardcoded_active_zero_removed(self):
        import inspect
        import amca.agent_fabric as fabric_mod

        src = inspect.getsource(fabric_mod.AgentFabricStore.catalog)
        self.assertNotIn('"active": 0', src)
        self.assertNotIn("'active': 0", src)

    def test_crash_uncertain_not_shown_active(self):
        org = OrganizationRun(organization_id="o", project_id="p", goal="g", budget_usd=5.0, max_concurrent=4)
        t = _task("t-uncertain", status=AgentTaskStatus.RUNNING)
        # Simulate crash-uncertain flag (recovery disposition).
        t.external_outcome_uncertain = True  # type: ignore[attr-defined]
        org.tasks[t.id] = t
        self.assertEqual(org.active_count(), 0)
        self.assertFalse(is_task_active({"status": "running", "external_outcome_uncertain": 1}))
        self.assertEqual(count_active([{"status": "running", "recovery_disposition": "BLOCKED_UNCERTAIN_EXTERNAL_OUTCOME"}]), 0)


class ActiveOrganizationTests(unittest.TestCase):
    def test_null_valid_if_none_explicit_status(self):
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        try:
            store = AgentFabricStore(Path(tmp.name) / "s.sqlite3")
            c = store.catalog()
            self.assertIsNone(c["active_organization"])
            self.assertIn(c["active_organization_status"], ("none", "unavailable", "unknown"))
            c2 = store.catalog(active_organizations=[], project_id="p-13h")
            self.assertIsNone(c2["active_organization"])
            self.assertEqual(c2["active_organization_status"], "none")
        finally:
            tmp.cleanup()

    def test_never_fabricated_unknown(self):
        from amca.agent_observability import resolve_active_organization

        r = resolve_active_organization(None)
        self.assertIsNone(r["active_organization"])
        self.assertEqual(r["active_organization_status"], "unavailable")
        r2 = resolve_active_organization([{"organization_id": "", "project_id": "p"}])
        self.assertIsNone(r2["active_organization"])
        self.assertEqual(r2["active_organization_status"], "unknown")


class LifecycleObservabilityTests(unittest.TestCase):
    def test_all_real_states_no_new_states(self):
        self.assertEqual(
            set(ALL_TASK_STATUSES),
            {"proposed", "authorized", "queued", "starting", "running",
             "waiting", "verifying", "completed", "merged", "retired",
             "failed", "cancelled"},
        )
        # AgentTaskStatus exposes exactly these 12 (no new states).
        states = {v for k, v in vars(AgentTaskStatus).items() if not k.startswith("_") and isinstance(v, str)}
        states.discard("ALLOWED_TRANSITIONS")
        self.assertEqual(states, set(ALL_TASK_STATUSES))
        counts = lifecycle_counts([{"status": s} for s in ALL_TASK_STATUSES])
        self.assertEqual(set(counts.keys()), set(ALL_TASK_STATUSES))

    def test_verifying_cancel_preserved_13d(self):
        t = _task("t-verify-13h")
        for nxt in (AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
                    AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
                    AgentTaskStatus.VERIFYING):
            t.transition(nxt)
        t.transition(AgentTaskStatus.CANCELLED)
        self.assertEqual(t.status, AgentTaskStatus.CANCELLED)
        with self.assertRaises(ValueError):
            t.transition(AgentTaskStatus.COMPLETED)


class IsolationTests(unittest.TestCase):
    def test_cross_project_telemetry_isolation(self):
        org_a = OrganizationRun(organization_id="o-a", project_id="proj-a", goal="g", budget_usd=5.0, max_concurrent=2)
        t = _task("t-a", project="proj-a", status=AgentTaskStatus.RUNNING)
        org_a.tasks[t.id] = t
        # Same-project allowed.
        snap = describe_organization(org_a, requesting_project_id="proj-a")
        self.assertEqual(snap["task_count"], 1)
        # Cross-project denied with no leak.
        with self.assertRaises(PermissionError):
            describe_organization(org_a, requesting_project_id="proj-b")

    def test_catalog_project_isolation(self):
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        try:
            store = AgentFabricStore(Path(tmp.name) / "s.sqlite3")
            store.register_capability_agent("proj-a", "Agent A", "role", ["s"], ["c"])
            org_a = OrganizationRun(organization_id="o-a", project_id="proj-a", goal="g", budget_usd=5.0, max_concurrent=2)
            org_b = OrganizationRun(organization_id="o-b", project_id="proj-b", goal="g", budget_usd=5.0, max_concurrent=2)
            ta = _task("ta", project="proj-a", status=AgentTaskStatus.RUNNING)
            tb = _task("tb", project="proj-b", status=AgentTaskStatus.RUNNING)
            org_a.tasks[ta.id] = ta
            org_b.tasks[tb.id] = tb
            ca = store.catalog(active_organizations=[org_a, org_b], project_id="proj-a")
            self.assertEqual(ca["counts"]["active"], 1)
            self.assertEqual(ca["active_organization"]["project_id"], "proj-a")
        finally:
            tmp.cleanup()


class RecoveryTests(unittest.TestCase):
    def test_cancelled_stays_cancelled_no_resurrect(self):
        from amca.agent_recovery import AgentRecoveryStore

        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        try:
            db = Path(tmp.name) / "state.sqlite3"
            store = AgentRecoveryStore(db)
            store.create_organization(organization_id="org-13h", project_id="p-13h",
                                      goal="g", budget_usd=5.0, max_concurrent=2)
            store.create_task(task_id="t-13h", organization_id="org-13h",
                              project_id="p-13h", agent_id="a", focus="f",
                              executor_kind=ExecutorKind.DETERMINISTIC_TOOL,
                              depends_on=[], verifier_ids=[], participant_ids=[])
            t = _task("t-13h", status=AgentTaskStatus.VERIFYING)
            t.transition(AgentTaskStatus.CANCELLED)
            store.update_task_status("t-13h", t.status, error="user cancel")
            rows = store.list_tasks_by_organization("org-13h")
            self.assertEqual(rows[0]["status"], "cancelled")
        finally:
            tmp.cleanup()


class Governance13CPreservedTests(unittest.TestCase):
    def setUp(self):
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        from amca.service import ServiceContext

        root = Path(__file__).resolve().parents[1]
        self.ctx = ServiceContext(root=root, database=Path(self.tmp.name) / "s.sqlite3",
                                  benchmarks=root / "benchmarks" / "tasks.json")
        governed = self.ctx.agent_fabric_store.save_custom_agent({
            "name": "PS1-13H Governed", "description": "probe",
            "instructions": "obey", "category": "Custom",
            "allowed_tools": ["artifact.author"], "permissions": [], "can_spawn": False,
        })
        restricted = self.ctx.agent_fabric_store.save_custom_agent({
            "name": "PS1-13H Restricted", "description": "probe",
            "instructions": "obey", "category": "Custom",
            "allowed_tools": ["filesystem.read"], "permissions": [], "can_spawn": False,
        })
        self.gov = str(governed["id"])
        self.res = str(restricted["id"])

    def tearDown(self):
        self.ctx.close()
        self.tmp.cleanup()

    def test_unauthorized_denied(self):
        with self.assertRaises(PermissionError):
            self.ctx.artifact_author("ps1-13h-a", {
                "artifact_type": "markdown", "title": "x", "content": "# x\nb",
                "created_by": "desktop_user", "agent_id": self.res})

    def test_spoof_denied(self):
        with self.assertRaises(PermissionError):
            self.ctx.artifact_author("ps1-13h-a", {
                "artifact_type": "markdown", "title": "spoof", "content": "# x\nb",
                "created_by": "desktop_user", "agent_id": self.res})

    def test_cross_project_denied(self):
        created = self.ctx.artifact_author("ps1-13h-a", {
            "artifact_type": "markdown", "title": "scoped", "content": "# s\nb",
            "created_by": "desktop_user", "agent_id": self.gov})
        aid = str(created["artifact_id"])
        with self.assertRaises(PermissionError):
            self.ctx.artifact_meta("ps1-13h-b", aid)


class NoForbiddenSurfaceTests(unittest.TestCase):
    def test_no_run_tree_api_no_health_agent_no_polling(self):
        import amca.agent_observability as obs

        names = " ".join(dir(obs)).casefold()
        for forbidden in ("run_tree", "runtree", "command_centre", "command_center", "health", "poll", "gauge"):
            self.assertNotIn(forbidden, names)
        import amca.service as svc

        # 13H discipline: the observability CORE must not smuggle in a run-tree
        # endpoint, health agent, polling, or command-centre. The bounded
        # run-tree API is authorized later by PS1-13J as its OWN module
        # (amca.agent_run_tree) + ServiceContext.agent_run_tree helper, so that
        # single helper is permitted here; health/poll/gauge/command-centre are
        # still forbidden everywhere.
        ctx_names = [n.casefold() for n in dir(svc.ServiceContext)]
        for forbidden in ("health_agent", "poll", "gauge", "command_centre", "command_center"):
            self.assertFalse([n for n in ctx_names if forbidden in n], forbidden)
        run_tree_helpers = [n for n in ctx_names if "run_tree" in n or "runtree" in n]
        self.assertTrue(set(run_tree_helpers) <= {"agent_run_tree"}, run_tree_helpers)


if __name__ == "__main__":
    unittest.main()
