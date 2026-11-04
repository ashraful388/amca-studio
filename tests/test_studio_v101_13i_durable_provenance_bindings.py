"""PS1-13I Durable Provenance Bindings (persistence/provenance only).

Maps org/run->task->agent/executor->model resolution->model call->tool
dispatch->tool attempt->artifact/result->verification and durably persists
ACTUAL executed model + governed tool bindings. No UI/chat/command-centre/
health, no provider/model routing, no state-machine redesign, no run-tree
API, no paid calls, V110 unchanged.

All mutable state under mandated scratch root
``D:\\ChatGPT\\AMCA\\.agent-tmp\\ps1-agent-13i``. Local-only, zero paid
calls. FAKE secret patterns only (never real credentials).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from amca.agent_fabric import AgentFabricStore
from amca.agent_observability import (
    describe_organization,
    sanitize_resolved_model,
    sanitize_tool_config,
    summarize_input,
)
from amca.agent_provenance import (
    BINDING_PATH_MAPPED,
    BINDING_PATH_STAGES,
    AgentProvenanceStore,
    describe_task_provenance,
    extract_executed_model,
    record_actual_model_binding,
)
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_runtime import (
    AgentTaskRecord,
    AgentTaskStatus,
    ExecutorKind,
    NanoScheduler,
    OrganizationRun,
)
from amca.models.base import ModelAdapter, ModelCapabilities, ModelResponse, TokenUsage

MANDATED_SCRATCH = Path(r"D:\ChatGPT\AMCA\.agent-tmp\ps1-agent-13i")

# FAKE-ONLY secret canaries (never real material).
FAKE_BEARER = "Bearer FAKE-13I-SECRET-99"
FAKE_API_KEY = "api_key=FAKE_ONLY_13I"
FAKE_PASSWORD = "password=NOT_REAL_13I"
FAKE_TOKEN = "token=FAKE_ONLY_13I"


class _FakeLocalAdapter(ModelAdapter):
    """Bounded local fake: zero cost, conservative, deterministic."""

    def __init__(self, adapter_id="fake-13i-local", provider="fake-local-provider",
                 model="fake-nano-13i", text="fake local output 13i"):
        super().__init__(
            adapter_id=adapter_id, provider=provider, model=model,
            capabilities=ModelCapabilities(is_local=True),
        )
        self._text = text
        self.calls = 0

    async def generate(self, request):
        async with self._call_scope(request):
            self.calls += 1
            return ModelResponse(
                text=self._text, model=self.model, provider=self.provider,
                usage=TokenUsage(input_tokens=5, output_tokens=7, cost_usd=0.0),
                finish_reason="stop",
            )

    def stream(self, request):
        raise NotImplementedError


class _FakeCloudAdapter(ModelAdapter):
    def __init__(self, adapter_id="fake-13i-cloud", provider="fake-cloud-provider",
                 model="fake-cloud-13i", text="fake cloud output 13i"):
        super().__init__(
            adapter_id=adapter_id, provider=provider, model=model,
            capabilities=ModelCapabilities(is_local=False),
        )
        self._text = text
        self.calls = 0

    async def generate(self, request):
        async with self._call_scope(request):
            self.calls += 1
            return ModelResponse(
                text=self._text, model=self.model, provider=self.provider,
                usage=TokenUsage(input_tokens=5, output_tokens=7, cost_usd=0.0),
                finish_reason="stop",
            )

    def stream(self, request):
        raise NotImplementedError


class _FailingAdapter(ModelAdapter):
    def __init__(self, adapter_id="fail-13i"):
        super().__init__(
            adapter_id=adapter_id, provider="fake-local-provider", model="fake-fail-13i",
            capabilities=ModelCapabilities(is_local=True),
        )
        self.calls = 0

    async def generate(self, request):
        async with self._call_scope(request):
            self.calls += 1
            raise RuntimeError("simulated 13i failure FAKE_ONLY")

    def stream(self, request):
        raise NotImplementedError


class _FailThenSucceedPrimary(ModelAdapter):
    """Primary that always fails (for fallback test)."""

    def __init__(self):
        super().__init__(
            adapter_id="primary-13i", provider="primary-provider-13i",
            model="primary-model-13i", capabilities=ModelCapabilities(is_local=True),
        )
        self.calls = 0

    async def generate(self, request):
        async with self._call_scope(request):
            self.calls += 1
            raise RuntimeError("primary unavailable")

    def stream(self, request):
        raise NotImplementedError


class _VerifierAdapter(ModelAdapter):
    def __init__(self, adapter_id, verdict="pass"):
        super().__init__(
            adapter_id=adapter_id, provider="fake-local-provider",
            model=f"verifier-{adapter_id}-13i",
            capabilities=ModelCapabilities(is_local=True),
        )
        self._verdict = verdict
        self.calls = 0

    async def generate(self, request):
        async with self._call_scope(request):
            self.calls += 1
            payload = json.dumps({
                "verdict": self._verdict,
                "summary": f"verifier {self.adapter_id} says {self._verdict}",
                "evidence_refs": [],
            })
            return ModelResponse(
                text=payload, model=self.model, provider=self.provider,
                usage=TokenUsage(cost_usd=0.0), finish_reason="stop",
            )

    def stream(self, request):
        raise NotImplementedError


def _tmp_db():
    MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
    db = Path(tmp.name) / "state.sqlite3"
    return tmp, db


def _scheduler(adapters, db, **kw):
    store = AgentRecoveryStore(db)
    sched = NanoScheduler(
        dict(adapters),
        budget_usd=kw.pop("budget_usd", 5.0),
        max_concurrent=kw.pop("max_concurrent", 4),
        max_attempts=kw.pop("max_attempts", 1),
        recovery_store=store,
        **kw,
    )
    return sched, store


class BindingPathMappedTests(unittest.TestCase):
    def test_path_stages_mapped(self):
        self.assertTrue(BINDING_PATH_MAPPED)
        self.assertEqual(
            list(BINDING_PATH_STAGES),
            ["organization", "task", "agent_executor", "model_resolution",
             "model_call", "tool_dispatch", "tool_attempt",
             "artifact_result", "verification"],
        )

    def test_describe_empty_never_fabricates(self):
        tmp, db = _tmp_db()
        try:
            prov = AgentProvenanceStore(db)
            path = describe_task_provenance(
                prov, organization_id="org-13i", project_id="p-13i",
                task_id="t-missing-13i", requesting_project_id="p-13i",
                task_row={"agent_id": "a", "executor_kind": "nano_local_model", "status": "queued"},
            )
            self.assertEqual(path["stages"], list(BINDING_PATH_STAGES))
            self.assertEqual(path["model_call"], [])
            self.assertEqual(path["tool_attempt"], [])
            self.assertEqual(path["artifact_result"], [])
            self.assertIsNone(path["verification"])
            self.assertFalse(path["bindings_present"]["model"])
            self.assertFalse(path["bindings_present"]["tool"])
        finally:
            tmp.cleanup()


class DurableModelBindingTests(unittest.TestCase):
    def test_actual_executed_model_persisted(self):
        tmp, db = _tmp_db()
        try:
            adapter = _FakeLocalAdapter()
            sched, store = _scheduler({"w-13i": adapter}, db)
            org = sched.schedule(
                [{"id": "t-13i-model", "agent_id": "w-13i",
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": "bounded 13i focus"}],
                organization_id="org-13i-model", project_id="p-13i", goal="g",
            )
            org = asyncio.run(sched.run(org))
            self.assertEqual(org.tasks["t-13i-model"].status, AgentTaskStatus.COMPLETED)
            prov = AgentProvenanceStore(db)
            rows = prov.list_model_bindings_for_task(
                "org-13i-model", "t-13i-model", requesting_project_id="p-13i")
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["provider_id"], "fake-local-provider")
            self.assertEqual(row["model_id"], "fake-nano-13i")
            self.assertEqual(row["locality"], "local")
            self.assertEqual(row["outcome"], "succeeded")
            self.assertEqual(row["adapter_id"], "fake-13i-local")
            self.assertIsNotNone(row["call_attempt_id"])
            self.assertIn("task:t-13i-model:attempt:1", row["call_key"])
            # No full payload duplication: preview bounded, digest present.
            self.assertIsNotNone(row["response_digest"])
            self.assertLessEqual(len(row["response_preview"] or ""), 200)
            # No credentials/headers/tokens columns.
            blob = json.dumps(row, default=str)
            self.assertNotIn("api_key", blob.lower())
            self.assertNotIn("authorization", blob.lower())
            self.assertNotIn("credential", blob.lower())
            # Call-attempt linkage (D): recovery row exists for same key.
            call = store.get_call_attempt("org-13i-model", "t-13i-model", 1, row["call_key"])
            self.assertIsNotNone(call)
            self.assertEqual(str(call["call_attempt_id"]), str(row["call_attempt_id"]))
        finally:
            tmp.cleanup()

    def test_extract_executed_model_prefers_actual(self):
        adapter = _FakeLocalAdapter(adapter_id="req-A", provider="provider-A", model="model-A")
        resp = ModelResponse(text="hi", model="model-B-actual",
                             provider="provider-B-actual", usage=TokenUsage())
        ident = extract_executed_model(adapter, resp)
        self.assertEqual(ident["provider_id"], "provider-B-actual")
        self.assertEqual(ident["model_id"], "model-B-actual")
        self.assertEqual(ident["locality"], "local")


class ActualExecutedFallbackTests(unittest.TestCase):
    def test_fallback_records_B_not_requested_A(self):
        tmp, db = _tmp_db()
        try:
            prov = AgentProvenanceStore(db)
            store = AgentRecoveryStore(db)
            store.create_organization(organization_id="org-13i-fb", project_id="p-13i",
                                      goal="g", budget_usd=5.0, max_concurrent=2)
            store.create_task(task_id="t-13i-fb", organization_id="org-13i-fb",
                              project_id="p-13i", agent_id="fallback-13i",
                              focus="fallback focus",
                              executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                              depends_on=[], verifier_ids=[], participant_ids=[])
            primary = _FailThenSucceedPrimary()
            fallback = _FakeLocalAdapter(adapter_id="fallback-13i",
                                         provider="fallback-provider-13i",
                                         model="fallback-model-13i",
                                         text="fallback ok")
            # Simulate router: try primary (fails), execute fallback (succeeds).
            from amca.models.base import ModelRequest
            try:
                asyncio.run(primary.generate(ModelRequest(messages="hi")))
            except RuntimeError:
                pass
            resp = asyncio.run(fallback.generate(ModelRequest(messages="hi")))
            # Record ONLY the actually executed fallback (never the requested primary).
            store.record_call_state(
                organization_id="org-13i-fb", task_id="t-13i-fb",
                task_attempt_number=1, call_key="task:t-13i-fb:attempt:1",
                state="RESPONSE_COMMITTED", executor_kind="nano_local_model",
                adapter_id="fallback-13i", reservation_id="",
                actual_cost_usd=0.0, response_payload="fallback ok",
            )
            row = record_actual_model_binding(
                prov, store, organization_id="org-13i-fb", project_id="p-13i",
                task_id="t-13i-fb", task_attempt=1,
                call_key="task:t-13i-fb:attempt:1", agent_id="fallback-13i",
                executor_kind="nano_local_model", adapter=fallback,
                response=resp, outcome="succeeded", cost_usd=0.0,
                response_text="fallback ok",
            )
            assert row is not None
            self.assertEqual(row["provider_id"], "fallback-provider-13i")
            self.assertEqual(row["model_id"], "fallback-model-13i")
            self.assertNotIn("primary-provider", json.dumps(row))
            self.assertNotIn("primary-model", json.dumps(row))
            self.assertEqual(primary.calls, 1)
            self.assertEqual(fallback.calls, 1)
        finally:
            tmp.cleanup()


class DurableToolBindingTests(unittest.TestCase):
    def test_governed_tool_linkage_no_payload_dup_no_secrets(self):
        tmp, db = _tmp_db()
        try:
            from amca.service import ServiceContext
            root = Path(__file__).resolve().parents[1]
            ctx = ServiceContext(root=root, database=db,
                                 benchmarks=root / "benchmarks" / "tasks.json")
            try:
                governed = ctx.agent_fabric_store.save_custom_agent({
                    "name": "PS1-13I Governed", "description": "probe",
                    "instructions": "obey", "category": "Custom",
                    "allowed_tools": ["artifact.author", "filesystem.read"],
                    "permissions": [], "can_spawn": False,
                })
                gid = str(governed["id"])
                caps = ctx.agent_fabric_store.tool_capabilities(gid)
                from amca.agent_fabric import AgentToolRouter
                decision = AgentToolRouter.authorize(gid, "artifact.author", caps)
                self.assertTrue(decision["authorized"])
                created = ctx.artifact_author("ps1-13i-proj", {
                    "artifact_type": "markdown", "title": "13i governed",
                    "content": "# 13i\nbounded body",
                    "created_by": "desktop_user", "agent_id": gid})
                aid = str(created["artifact_id"])
                prov = AgentProvenanceStore(db)
                # Linkage: org/run->task->agent->tool->attempt->result.
                row = prov.record_tool_binding(
                    organization_id="org-13i-tool", project_id="ps1-13i-proj",
                    task_id="t-13i-tool", task_attempt=1, agent_id=gid,
                    executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                    tool_id="artifact.author", operation="author",
                    outcome="succeeded", tool_execution_id=f"exec-13i-{aid[:8]}",
                    capability_ref=json.dumps(sorted(caps))[:500],
                    authz_ref=json.dumps(decision)[:500],
                    result_ref=aid, result_text="governed author ok",
                )
                self.assertEqual(row["tool_id"], "artifact.author")
                self.assertEqual(row["outcome"], "succeeded")
                self.assertEqual(row["result_ref"], aid)
                self.assertIsNotNone(row["result_digest"])
                self.assertLessEqual(len(row["result_preview"] or ""), 200)
                blob = json.dumps(row, default=str)
                self.assertNotIn("FAKE_ONLY", blob)
                # Denied bindings must not carry results (never fabricate success).
                with self.assertRaises(ValueError):
                    prov.record_tool_binding(
                        organization_id="org-13i-tool", project_id="ps1-13i-proj",
                        task_id="t-13i-tool", task_attempt=1, agent_id=gid,
                        executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                        tool_id="artifact.author", operation="author",
                        outcome="denied", tool_execution_id="exec-denied-13i",
                        result_ref=aid, result_text="must not persist",
                    )
            finally:
                ctx.close()
        finally:
            tmp.cleanup()


class MultiCallProvenanceTests(unittest.TestCase):
    def test_verification_cell_two_bindings_via_call_attempts(self):
        tmp, db = _tmp_db()
        try:
            v1 = _VerifierAdapter("v1-13i")
            v2 = _VerifierAdapter("v2-13i")
            sched, _store = _scheduler({"v1-13i": v1, "v2-13i": v2}, db)
            org = sched.schedule(
                [{"id": "t-13i-multi", "agent_id": "cell-13i",
                  "executor_kind": ExecutorKind.VERIFICATION_CELL,
                  "verifier_ids": ["v1-13i", "v2-13i"],
                  "focus": "verify bounded 13i"}],
                organization_id="org-13i-multi", project_id="p-13i", goal="g",
            )
            org = asyncio.run(sched.run(org))
            self.assertEqual(org.tasks["t-13i-multi"].status, AgentTaskStatus.COMPLETED)
            prov = AgentProvenanceStore(db)
            rows = prov.list_model_bindings_for_task(
                "org-13i-multi", "t-13i-multi", requesting_project_id="p-13i")
            self.assertEqual(len(rows), 2)
            keys = sorted(r["call_key"] for r in rows)
            self.assertEqual(keys, ["verifier:v1-13i", "verifier:v2-13i"])
            for row in rows:
                self.assertEqual(row["outcome"], "succeeded")
                self.assertIsNotNone(row["call_attempt_id"])
        finally:
            tmp.cleanup()


class FailedRunProvenanceTests(unittest.TestCase):
    def test_failed_run_no_fabricated_success_binding(self):
        tmp, db = _tmp_db()
        try:
            bad = _FailingAdapter()
            sched, _store = _scheduler({"bad-13i": bad}, db, max_attempts=1)
            org = sched.schedule(
                [{"id": "t-13i-fail", "agent_id": "bad-13i",
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": "will fail"}],
                organization_id="org-13i-fail", project_id="p-13i", goal="g",
            )
            org = asyncio.run(sched.run(org))
            self.assertEqual(org.tasks["t-13i-fail"].status, AgentTaskStatus.FAILED)
            prov = AgentProvenanceStore(db)
            rows = prov.list_model_bindings_for_task(
                "org-13i-fail", "t-13i-fail", requesting_project_id="p-13i")
            # Never fabricate an unexecuted success binding.
            self.assertEqual([r for r in rows if r["outcome"] == "succeeded"], [])
            path = describe_task_provenance(
                prov, organization_id="org-13i-fail", project_id="p-13i",
                task_id="t-13i-fail", requesting_project_id="p-13i",
                task_row={"agent_id": "bad-13i", "executor_kind": "nano_local_model",
                          "status": "failed"},
            )
            self.assertEqual(path["task"]["status"], "failed")
            self.assertFalse(path["bindings_present"]["model"])
        finally:
            tmp.cleanup()


class CancelledRunProvenanceTests(unittest.TestCase):
    def test_verifying_cancel_preserves_binding_no_fabrication(self):
        tmp, db = _tmp_db()
        try:
            prov = AgentProvenanceStore(db)
            store = AgentRecoveryStore(db)
            store.create_organization(organization_id="org-13i-cancel",
                                      project_id="p-13i", goal="g",
                                      budget_usd=5.0, max_concurrent=2)
            store.create_task(task_id="t-13i-cancel", organization_id="org-13i-cancel",
                              project_id="p-13i", agent_id="w-13i",
                              focus="cancel focus",
                              executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                              depends_on=[], verifier_ids=[], participant_ids=[])
            adapter = _FakeLocalAdapter()
            store.record_call_state(
                organization_id="org-13i-cancel", task_id="t-13i-cancel",
                task_attempt_number=1, call_key="task:t-13i-cancel:attempt:1",
                state="RESPONSE_COMMITTED", executor_kind="nano_local_model",
                adapter_id="fake-13i-local", reservation_id="",
                actual_cost_usd=0.0, response_payload="ok",
            )
            row = record_actual_model_binding(
                prov, store, organization_id="org-13i-cancel", project_id="p-13i",
                task_id="t-13i-cancel", task_attempt=1,
                call_key="task:t-13i-cancel:attempt:1", agent_id="w-13i",
                executor_kind="nano_local_model", adapter=adapter,
                response=ModelResponse(text="ok", model=adapter.model,
                                       provider=adapter.provider,
                                       usage=TokenUsage()),
                outcome="succeeded", cost_usd=0.0, response_text="ok",
            )
            self.assertIsNotNone(row)
            # VERIFYING-cancel via canonical path (13D preserved).
            task = AgentTaskRecord(id="t-13i-cancel", organization_id="org-13i-cancel",
                                   project_id="p-13i", agent_id="w-13i",
                                   focus="cancel focus")
            for nxt in (AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
                        AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
                        AgentTaskStatus.VERIFYING):
                task.transition(nxt)
            task.transition(AgentTaskStatus.CANCELLED)
            self.assertEqual(task.status, AgentTaskStatus.CANCELLED)
            store.update_task_status("t-13i-cancel", task.status, error="user cancel")
            # Binding survives cancel; no new tool bindings fabricated.
            rows = prov.list_model_bindings_for_task(
                "org-13i-cancel", "t-13i-cancel", requesting_project_id="p-13i")
            self.assertEqual(len(rows), 1)
            tools = prov.list_tool_bindings_for_task(
                "org-13i-cancel", "t-13i-cancel", requesting_project_id="p-13i")
            self.assertEqual(tools, [])
            path = describe_task_provenance(
                prov, organization_id="org-13i-cancel", project_id="p-13i",
                task_id="t-13i-cancel", requesting_project_id="p-13i",
                task_row={"agent_id": "w-13i", "executor_kind": "nano_local_model",
                          "status": task.status},
            )
            self.assertEqual(path["task"]["status"], "cancelled")
            self.assertTrue(path["bindings_present"]["model"])
            self.assertFalse(path["bindings_present"]["tool"])
            with self.assertRaises(ValueError):
                task.transition(AgentTaskStatus.COMPLETED)
        finally:
            tmp.cleanup()


class RestartDurabilityTests(unittest.TestCase):
    def test_bounded_local_model_plus_governed_tool_survives_reopen(self):
        tmp, db = _tmp_db()
        try:
            from amca.service import ServiceContext
            root = Path(__file__).resolve().parents[1]
            # Live bounded local task with model call.
            adapter = _FakeLocalAdapter(text="restart durable 13i")
            sched, _store = _scheduler({"w-13i": adapter}, db)
            org = sched.schedule(
                [{"id": "t-13i-restart", "agent_id": "w-13i",
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": "restart bounded focus"}],
                organization_id="org-13i-restart", project_id="ps1-13i-restart",
                goal="restart probe",
            )
            org = asyncio.run(sched.run(org))
            self.assertEqual(org.tasks["t-13i-restart"].status, AgentTaskStatus.COMPLETED)
            # Governed tool execution linked to the same org/task.
            ctx = ServiceContext(root=root, database=db,
                                 benchmarks=root / "benchmarks" / "tasks.json")
            try:
                governed = ctx.agent_fabric_store.save_custom_agent({
                    "name": "PS1-13I Restart", "description": "probe",
                    "instructions": "obey", "category": "Custom",
                    "allowed_tools": ["artifact.author"], "permissions": [],
                    "can_spawn": False,
                })
                gid = str(governed["id"])
                created = ctx.artifact_author("ps1-13i-restart", {
                    "artifact_type": "markdown", "title": "restart 13i",
                    "content": "# restart\nbody", "created_by": "desktop_user",
                    "agent_id": gid})
                aid = str(created["artifact_id"])
                prov = AgentProvenanceStore(db)
                # Model binding already auto-persisted by scheduler run.
                models_before = prov.list_model_bindings_for_task(
                    "org-13i-restart", "t-13i-restart",
                    requesting_project_id="ps1-13i-restart")
                self.assertEqual(len(models_before), 1)
                prov.record_tool_binding(
                    organization_id="org-13i-restart", project_id="ps1-13i-restart",
                    task_id="t-13i-restart", task_attempt=1, agent_id=gid,
                    executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                    tool_id="artifact.author", operation="author",
                    outcome="succeeded", tool_execution_id="exec-restart-13i",
                    capability_ref="artifact.author", authz_ref="router:allow",
                    call_attempt_id=models_before[0]["call_attempt_id"],
                    call_key=models_before[0]["call_key"],
                    result_ref=aid, result_text="restart tool ok",
                )
                models_snap = list(models_before)
                tools_snap = prov.list_tool_bindings_for_task(
                    "org-13i-restart", "t-13i-restart",
                    requesting_project_id="ps1-13i-restart")
            finally:
                ctx.close()
            # Reopen store (new instances, same file) -> bindings intact.
            prov2 = AgentProvenanceStore(db)
            store2 = AgentRecoveryStore(db)
            self.assertIsNotNone(store2.get_organization("org-13i-restart"))
            models_after = prov2.list_model_bindings_for_task(
                "org-13i-restart", "t-13i-restart",
                requesting_project_id="ps1-13i-restart")
            tools_after = prov2.list_tool_bindings_for_task(
                "org-13i-restart", "t-13i-restart",
                requesting_project_id="ps1-13i-restart")
            self.assertEqual(len(models_after), len(models_snap))
            self.assertEqual(models_after[0]["provider_id"], models_snap[0]["provider_id"])
            self.assertEqual(models_after[0]["model_id"], models_snap[0]["model_id"])
            self.assertEqual(len(tools_after), 1)
            self.assertEqual(tools_after[0]["result_ref"], tools_snap[0]["result_ref"])
        finally:
            tmp.cleanup()


class IsolationTests(unittest.TestCase):
    def test_cross_project_no_leakage(self):
        tmp, db = _tmp_db()
        try:
            prov = AgentProvenanceStore(db)
            store = AgentRecoveryStore(db)
            store.create_organization(organization_id="org-13i-iso",
                                      project_id="proj-a-13i", goal="g",
                                      budget_usd=5.0, max_concurrent=2)
            store.create_task(task_id="t-13i-iso", organization_id="org-13i-iso",
                              project_id="proj-a-13i", agent_id="a",
                              focus="iso", executor_kind="nano_local_model",
                              depends_on=[], verifier_ids=[], participant_ids=[])
            store.record_call_state(
                organization_id="org-13i-iso", task_id="t-13i-iso",
                task_attempt_number=1, call_key="task:t-13i-iso:attempt:1",
                state="RESPONSE_COMMITTED", executor_kind="nano_local_model",
                adapter_id="a", reservation_id="", actual_cost_usd=0.0,
                response_payload="ok",
            )
            prov.record_model_binding(
                organization_id="org-13i-iso", project_id="proj-a-13i",
                task_id="t-13i-iso", task_attempt=1,
                call_key="task:t-13i-iso:attempt:1", agent_id="a",
                executor_kind="nano_local_model", adapter_id="a",
                provider_id="p-a", model_id="m-a", is_local=True,
                outcome="succeeded", response_text="ok",
            )
            prov.record_tool_binding(
                organization_id="org-13i-iso", project_id="proj-a-13i",
                task_id="t-13i-iso", task_attempt=1, agent_id="a",
                executor_kind="nano_local_model", tool_id="filesystem.read",
                operation="read", outcome="succeeded",
                tool_execution_id="exec-iso-13i", result_text="ok",
            )
            # Same-project allowed.
            self.assertEqual(len(prov.list_model_bindings_for_task(
                "org-13i-iso", "t-13i-iso", requesting_project_id="proj-a-13i")), 1)
            # Cross-project denied with no leak.
            with self.assertRaises(PermissionError) as ctx1:
                prov.list_model_bindings_for_task(
                    "org-13i-iso", "t-13i-iso", requesting_project_id="proj-b-13i")
            self.assertNotIn("t-13i-iso", str(ctx1.exception))
            with self.assertRaises(PermissionError):
                prov.list_tool_bindings_for_task(
                    "org-13i-iso", "t-13i-iso", requesting_project_id="proj-b-13i")
            with self.assertRaises(PermissionError):
                describe_task_provenance(
                    prov, organization_id="org-13i-iso", project_id="proj-a-13i",
                    task_id="t-13i-iso", requesting_project_id="proj-b-13i")
        finally:
            tmp.cleanup()


class SecretSafetyTests(unittest.TestCase):
    def test_fake_secrets_never_persisted(self):
        tmp, db = _tmp_db()
        try:
            prov = AgentProvenanceStore(db)
            store = AgentRecoveryStore(db)
            store.create_organization(organization_id="org-13i-sec",
                                      project_id="p-13i-sec", goal="g",
                                      budget_usd=5.0, max_concurrent=2)
            store.create_task(task_id="t-13i-sec", organization_id="org-13i-sec",
                              project_id="p-13i-sec", agent_id="a",
                              focus=f"focus {FAKE_BEARER} {FAKE_API_KEY}",
                              executor_kind="nano_local_model",
                              depends_on=[], verifier_ids=[], participant_ids=[])
            store.record_call_state(
                organization_id="org-13i-sec", task_id="t-13i-sec",
                task_attempt_number=1, call_key="task:t-13i-sec:attempt:1",
                state="RESPONSE_COMMITTED", executor_kind="nano_local_model",
                adapter_id="a", reservation_id="", actual_cost_usd=0.0,
                response_payload="ok",
            )
            mrow = prov.record_model_binding(
                organization_id="org-13i-sec", project_id="p-13i-sec",
                task_id="t-13i-sec", task_attempt=1,
                call_key="task:t-13i-sec:attempt:1", agent_id="a",
                executor_kind="nano_local_model", adapter_id="a",
                provider_id="p", model_id="m", is_local=True,
                outcome="succeeded",
                response_text=f"output {FAKE_BEARER} {FAKE_API_KEY} {FAKE_PASSWORD} {FAKE_TOKEN}",
            )
            trow = prov.record_tool_binding(
                organization_id="org-13i-sec", project_id="p-13i-sec",
                task_id="t-13i-sec", task_attempt=1, agent_id="a",
                executor_kind="nano_local_model", tool_id="filesystem.read",
                operation="read", outcome="succeeded",
                tool_execution_id="exec-sec-13i",
                result_text=f"result {FAKE_BEARER} {FAKE_API_KEY}",
            )
            blob = json.dumps([mrow, trow], default=str)
            self.assertNotIn("FAKE-13I-SECRET-99", blob)
            self.assertNotIn("FAKE_ONLY_13I", blob)
            self.assertNotIn("NOT_REAL_13I", blob)
            path = describe_task_provenance(
                prov, organization_id="org-13i-sec", project_id="p-13i-sec",
                task_id="t-13i-sec", requesting_project_id="p-13i-sec",
                task_row={"agent_id": "a", "executor_kind": "nano_local_model",
                          "status": "completed"},
            )
            self.assertNotIn("FAKE_ONLY_13I", json.dumps(path, default=str))
            self.assertNotIn("FAKE-13I-SECRET-99", json.dumps(path, default=str))
        finally:
            tmp.cleanup()


class Preserve13C13D13HTests(unittest.TestCase):
    def test_13c_spoof_still_denied(self):
        tmp, db = _tmp_db()
        try:
            from amca.service import ServiceContext
            root = Path(__file__).resolve().parents[1]
            ctx = ServiceContext(root=root, database=db,
                                 benchmarks=root / "benchmarks" / "tasks.json")
            try:
                restricted = ctx.agent_fabric_store.save_custom_agent({
                    "name": "PS1-13I Restricted", "description": "probe",
                    "instructions": "obey", "category": "Custom",
                    "allowed_tools": ["filesystem.read"], "permissions": [],
                    "can_spawn": False,
                })
                with self.assertRaises(PermissionError):
                    ctx.artifact_author("ps1-13i-p", {
                        "artifact_type": "markdown", "title": "spoof",
                        "content": "# x\nbody", "created_by": "desktop_user",
                        "agent_id": str(restricted["id"])})
            finally:
                ctx.close()
        finally:
            tmp.cleanup()

    def test_13d_verifying_cancel_preserved(self):
        task = AgentTaskRecord(id="t-13i-13d", organization_id="o",
                               project_id="p", agent_id="a", focus="f")
        for nxt in (AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
                    AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
                    AgentTaskStatus.VERIFYING):
            task.transition(nxt)
        task.transition(AgentTaskStatus.CANCELLED)
        self.assertEqual(task.status, AgentTaskStatus.CANCELLED)
        with self.assertRaises(ValueError):
            task.transition(AgentTaskStatus.COMPLETED)

    def test_13h_serializers_no_raw_payload(self):
        task = AgentTaskRecord(id="t-13i-13h", organization_id="o",
                               project_id="p", agent_id="a", focus="f")
        task.tool_config = {"ok": "yes", "secret": "FAKE_ONLY_13I"}
        task.resolved_model_config = {"model_id": "m", "provider": "p",
                                      "is_local": True, "api_key": "FAKE_ONLY_13I"}
        task.input_payload = {"q": "hi", "x": FAKE_BEARER}
        d = task.to_dict()
        blob = json.dumps(d, default=str)
        self.assertNotIn("FAKE_ONLY_13I", blob)
        self.assertNotIn("FAKE-13I-SECRET-99", blob)
        self.assertNotIn("input_payload", d)
        for key in ("tool_config", "output_schema", "resolved_model",
                    "input_summary", "timing"):
            self.assertIn(key, d)
        self.assertEqual(sanitize_tool_config({"a": "b"}), {"a": "b"})
        self.assertIsNotNone(sanitize_resolved_model({"model_id": "m"}))
        self.assertIsNotNone(summarize_input({"q": "hi"}))
        # Lifecycle counts additive, no new states.
        org = OrganizationRun(organization_id="o", project_id="p",
                              goal="g", budget_usd=5.0, max_concurrent=2)
        self.assertIn("lifecycle_counts", org.to_dict())


class Func02Tests(unittest.TestCase):
    def test_func02_fixed_when_13h_fields_and_bindings(self):
        tmp, db = _tmp_db()
        try:
            # 13H fields present?
            task = AgentTaskRecord(id="t-13i-func02", organization_id="o",
                                   project_id="p", agent_id="a", focus="f")
            d = task.to_dict()
            has_13h = all(k in d for k in ("tool_config", "resolved_model",
                                           "input_summary", "timing"))
            # Durable bindings present?
            prov = AgentProvenanceStore(db)
            store = AgentRecoveryStore(db)
            store.create_organization(organization_id="org-13i-f02",
                                      project_id="p", goal="g",
                                      budget_usd=5.0, max_concurrent=2)
            store.create_task(task_id="t-13i-f02", organization_id="org-13i-f02",
                              project_id="p", agent_id="a", focus="f",
                              executor_kind="nano_local_model",
                              depends_on=[], verifier_ids=[], participant_ids=[])
            store.record_call_state(
                organization_id="org-13i-f02", task_id="t-13i-f02",
                task_attempt_number=1, call_key="task:t-13i-f02:attempt:1",
                state="RESPONSE_COMMITTED", executor_kind="nano_local_model",
                adapter_id="a", reservation_id="", actual_cost_usd=0.0,
                response_payload="ok",
            )
            prov.record_model_binding(
                organization_id="org-13i-f02", project_id="p",
                task_id="t-13i-f02", task_attempt=1,
                call_key="task:t-13i-f02:attempt:1", agent_id="a",
                executor_kind="nano_local_model", adapter_id="a",
                provider_id="p", model_id="m", is_local=True,
                outcome="succeeded", response_text="ok",
            )
            has_bindings = bool(prov.list_model_bindings_for_task(
                "org-13i-f02", "t-13i-f02", requesting_project_id="p"))
            status = "FIXED" if (has_13h and has_bindings) else "PARTIALLY_FIXED"
            self.assertTrue(has_13h)
            self.assertTrue(has_bindings)
            self.assertEqual(status, "FIXED")
        finally:
            tmp.cleanup()


class NoForbiddenSurfaceTests(unittest.TestCase):
    def test_no_run_tree_no_health_no_routing(self):
        import amca.agent_provenance as prov_mod

        names = " ".join(dir(prov_mod)).casefold()
        for forbidden in ("run_tree", "runtree", "command_centre",
                          "command_center", "health", "poll", "gauge"):
            self.assertNotIn(forbidden, names)
        import inspect
        src = inspect.getsource(prov_mod)
        for forbidden in ("run_tree", "command_centre", "command_center"):
            self.assertNotIn(forbidden, src.casefold())
        # No provider/model routing logic in provenance module.
        for forbidden in ("select_model", "route_model", "provider_router"):
            self.assertNotIn(forbidden, src.casefold())


if __name__ == "__main__":
    unittest.main()
