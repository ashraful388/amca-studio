"""PS1-13K Agent Run Summary + Query Efficiency.

Compact truthful summary endpoint derived from EXISTING 13H/13I persistence,
plus proof that the 13J run-tree no longer exhibits an N+1 read pattern.
Security parity with 13J (foreign == nonexistent; no payload/secret leak).
Backend only: no UI, no chat disclosure, no command-centre, no polling,
no provider/model changes, no paid calls, V110 unchanged. FAKE-only canaries.

Scratch under mandated root ``D:\\ChatGPT\\AMCA\\.agent-tmp\\ps1-agent-13k``.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.agent_provenance import AgentProvenanceStore
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_run_tree import (
    RUN_SUMMARY_SCHEMA,
    RUN_TREE_SCHEMA,
    UnknownRunOrganization,
    build_run_summary,
    build_run_tree,
)
from amca.service import create_server

MANDATED_SCRATCH = Path(r"D:\ChatGPT\AMCA\.agent-tmp\ps1-agent-13k")
FAKE_BEARER = "Bearer TEST-SECRET-123"
FAKE_API_KEY = "api_key=FAKE_ONLY"


def _count_selects(fn):
    """Run fn() and return (result, select_query_count) with real tracing."""
    original = sqlite3.connect
    counter = {"n": 0}

    def traced(*a, **k):
        conn = original(*a, **k)
        conn.set_trace_callback(
            lambda s: counter.__setitem__(
                "n", counter["n"] + 1
                if s.strip().upper().startswith("SELECT") and "PRAGMA" not in s
                else counter["n"]
            )
        )
        return conn

    sqlite3.connect = traced
    try:
        result = fn()
    finally:
        sqlite3.connect = original
    return result, counter["n"]


class SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="sum-", dir=MANDATED_SCRATCH))
        self.database = self.workdir / "amca.db"
        self.recovery = AgentRecoveryStore(self.database)
        self.provenance = AgentProvenanceStore(self.database)
        self.project = "p-13k"
        self.org = "o-13k"
        self.recovery.create_organization(
            organization_id=self.org, project_id=self.project,
            goal=f"g {FAKE_BEARER}", budget_usd=2.0, max_concurrent=4,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _task(self, task_id, *, agent_id=None, parent=None, status="completed", depth=0):
        self.recovery.create_task(
            task_id=task_id, organization_id=self.org, project_id=self.project,
            agent_id=agent_id or f"a-{task_id}", focus="f",
            executor_kind="manager" if depth == 0 else "nano_local_model",
            manager_depth=depth, proposed_by_manager_id=parent,
        )
        self.recovery.update_task_status(task_id, status)

    def _model(self, task_id, model_id, provider="ollama", attempt=1, call_key="model:main"):
        self.provenance.record_model_binding(
            organization_id=self.org, project_id=self.project, task_id=task_id,
            task_attempt=attempt, call_key=call_key, agent_id=f"a-{task_id}",
            executor_kind="nano_local_model", adapter_id=f"ad-{model_id}",
            provider_id=provider, model_id=model_id, is_local=True,
            outcome="succeeded",
        )

    def _tool(self, task_id, tool_id, *, result_ref=None, outcome="succeeded",
              attempt=1, operation="run"):
        self.provenance.record_tool_binding(
            organization_id=self.org, project_id=self.project, task_id=task_id,
            task_attempt=attempt, agent_id=f"a-{task_id}",
            executor_kind="nano_local_model", tool_id=tool_id, operation=operation,
            outcome=outcome, tool_execution_id=f"tx-{task_id}-{tool_id}-{attempt}",
            result_ref=result_ref,
        )

    def _summary(self, **kw):
        return build_run_summary(self.recovery, self.provenance, self.org,
                                 requesting_project_id=self.project, **kw)

    # -- B. chat-disclosure truth ---------------------------------------------

    def test_truthful_distinct_counts(self) -> None:
        self._task("mgr", depth=0)
        self._task("k1", parent="a-mgr", depth=1)
        self._task("k2", parent="a-mgr", depth=1)
        # two distinct actual executed models; k2 fell back to a different one
        self._model("k1", "qwen3:1.7b")
        self._model("k2", "qwen3:1.7b")
        self._model("k2", "llama3.2:3b", call_key="model:fallback")
        # two distinct tools, two artifacts
        self._tool("k1", "filesystem.read")
        self._tool("k1", "artifact.export", result_ref="art-1")
        self._tool("k2", "artifact.export", result_ref="art-2")
        s = self._summary()
        self.assertEqual(s["schema"], RUN_SUMMARY_SCHEMA)
        self.assertEqual(s["agent_count"], 3)  # mgr + k1 + k2 distinct agents
        self.assertEqual(s["model_count"], 2)  # distinct executed (provider,model)
        self.assertEqual(s["tool_count"], 2)   # filesystem.read + artifact.export
        self.assertEqual(s["artifact_count"], 2)
        self.assertEqual(s["task_count"], 3)

    def test_failed_and_active_counts(self) -> None:
        self._task("mgr", depth=0, status="running")
        self._task("bad", parent="a-mgr", status="failed", depth=1)
        self._task("done", parent="a-mgr", status="completed", depth=1)
        s = self._summary()
        self.assertEqual(s["failed_task_count"], 1)
        self.assertEqual(s["active_agent_count"], 1)  # only the running manager
        self.assertEqual(s["run_state"], "active")

    def test_counts_ignore_configured_only(self) -> None:
        # Task with no executed model/tool bindings adds nothing to counts.
        self._task("mgr", depth=0, status="completed")
        self._task("k1", parent="a-mgr", status="completed", depth=1)
        s = self._summary()
        self.assertEqual(s["model_count"], 0)
        self.assertEqual(s["tool_count"], 0)
        self.assertEqual(s["artifact_count"], 0)

    # -- C. security parity -----------------------------------------------------

    def test_foreign_and_nonexistent_indistinguishable(self) -> None:
        self._task("mgr", depth=0)
        with self.assertRaises(UnknownRunOrganization) as f:
            build_run_summary(self.recovery, self.provenance, self.org,
                              requesting_project_id="p-other")
        with self.assertRaises(UnknownRunOrganization) as m:
            build_run_summary(self.recovery, self.provenance, "o-nope",
                              requesting_project_id=self.project)
        self.assertEqual(str(f.exception), str(m.exception))

    def test_no_secret_or_payload_leak(self) -> None:
        self._task("mgr", depth=0)
        self._model("mgr", "qwen3:1.7b")
        self.recovery.record_call_state(
            organization_id=self.org, task_id="mgr", task_attempt_number=1,
            call_key="model:main", state="RESPONSE_COMMITTED",
            executor_kind="manager", adapter_id="ad", reservation_id="r1",
            response_payload=f"raw {FAKE_BEARER} {FAKE_API_KEY}",
        )
        blob = json.dumps(self._summary())
        self.assertNotIn("TEST-SECRET-123", blob)
        self.assertNotIn("FAKE_ONLY", blob)
        self.assertNotIn("response_payload", blob)
        self.assertNotIn("input_payload", blob)
        self.assertNotIn("raw", blob.split('"task_count"')[0])

    # -- G. summary/tree consistency -------------------------------------------

    def test_summary_matches_untruncated_tree(self) -> None:
        self._task("mgr", depth=0)
        for i in range(4):
            self._task(f"k{i}", parent="a-mgr", depth=1)
            self._model(f"k{i}", f"m-{i}")
            self._tool(f"k{i}", "filesystem.read", result_ref=f"art-{i}")
        tree = build_run_tree(self.recovery, self.provenance, self.org,
                              requesting_project_id=self.project)
        s = self._summary()
        self.assertFalse(tree["truncated"])
        self.assertEqual(s["task_count"], tree["total_tasks"])
        # tree includes 5 nodes; task_count matches; artifacts match.
        artifacts = {a for n in _walk(tree) for a in n["artifact_refs"]}
        self.assertEqual(len(artifacts), s["artifact_count"])

    def test_summary_covers_full_run_when_tree_truncated(self) -> None:
        self._task("mgr", depth=0)
        for i in range(30):
            self._task(f"k{i}", parent="a-mgr", depth=1)
            self._tool(f"k{i}", "filesystem.read", result_ref=f"art-{i}")
        tree = build_run_tree(self.recovery, self.provenance, self.org,
                              requesting_project_id=self.project, max_nodes=10)
        s = self._summary()
        self.assertTrue(tree["truncated"])
        self.assertLess(tree["included_nodes"], s["task_count"])
        self.assertEqual(s["task_count"], 31)      # full run
        self.assertEqual(s["artifact_count"], 30)  # full run, not first 10

    # -- D/E. query efficiency ---------------------------------------------------

    def test_summary_query_count_bounded(self) -> None:
        self._task("mgr", depth=0)
        for i in range(40):
            self._task(f"k{i}", parent="a-mgr", depth=1)
            self._model(f"k{i}", "m-x")
            self._tool(f"k{i}", "filesystem.read", result_ref=f"art-{i}")
        _, queries = _count_selects(lambda: self._summary())
        # Fixed bounded reads (org, tasks, model, tool, + per-binding verifies)
        # must NOT scale with task count: assert well under one-query-per-task.
        self.assertLess(queries, 41)

    def test_tree_query_count_bounded_no_n_plus_one(self) -> None:
        self._task("mgr", depth=0)
        for i in range(60):
            self._task(f"k{i}", parent="a-mgr", depth=1)
            self.recovery.record_call_state(
                organization_id=self.org, task_id=f"k{i}", task_attempt_number=1,
                call_key="c", state="SETTLED", executor_kind="nano_local_model",
                adapter_id="ad", reservation_id="r")
            self.recovery.record_verdict(
                organization_id=self.org, task_id=f"k{i}", task_attempt_number=1,
                verifier_id="v1", verdict="verified", summary="ok",
                evidence_refs_json=[])
        _, queries = _count_selects(
            lambda: build_run_tree(self.recovery, self.provenance, self.org,
                                   requesting_project_id=self.project, max_nodes=250)
        )
        # Bounded: constant number of SELECTs regardless of 61 tasks. N+1 would
        # be ~2*61=122+; batching keeps it under a dozen.
        self.assertLess(queries, 20)

    # -- H. lifecycle truth ------------------------------------------------------

    def test_run_state_lifecycle_fidelity(self) -> None:
        self._task("mgr", depth=0, status="completed")
        self.assertEqual(self._summary()["run_state"], "completed")

    def test_run_state_cancelled_when_all_cancelled(self) -> None:
        self._task("mgr", depth=0, status="cancelled")
        s = self._summary()
        self.assertEqual(s["run_state"], "cancelled")
        self.assertNotIn("suspended", s["lifecycle_counts"])

    # -- I. 13J still works after batching --------------------------------------

    def test_tree_bindings_intact_after_batching(self) -> None:
        self._task("mgr", depth=0)
        self._task("k1", parent="a-mgr", depth=1)
        self._model("k1", "qwen3:1.7b")
        self._tool("k1", "artifact.export", result_ref="art-9")
        tree = build_run_tree(self.recovery, self.provenance, self.org,
                              requesting_project_id=self.project)
        self.assertEqual(tree["schema"], RUN_TREE_SCHEMA)
        node = next(n for n in _walk(tree) if n["task_id"] == "k1")
        self.assertEqual(node["model_bindings"][0]["model_id"], "qwen3:1.7b")
        self.assertEqual(node["artifact_refs"], ["art-9"])


def _walk(tree_or_node):
    if isinstance(tree_or_node, dict) and "roots" in tree_or_node:
        nodes = []
        for root in tree_or_node["roots"]:
            nodes.extend(_walk(root))
        return nodes
    out = [tree_or_node]
    for child in tree_or_node.get("children", []):
        out.extend(_walk(child))
    return out


class SummaryHttpRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="http-", dir=MANDATED_SCRATCH))
        self.database = self.workdir / "amca.db"
        self.root = Path(__file__).resolve().parents[1]
        self.server = create_server(host="127.0.0.1", port=0, root=self.root,
                                    database=self.database)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = str(self._request("/api/session", authorized=False)[1]["session_token"])
        recovery = AgentRecoveryStore(self.database)
        self.project = "p-13k-http"
        self.org = "o-13k-http"
        recovery.create_organization(organization_id=self.org, project_id=self.project,
                                     goal="http", budget_usd=1.0, max_concurrent=2)
        recovery.create_task(task_id="t-mgr", organization_id=self.org,
                             project_id=self.project, agent_id="a-mgr", focus="mgr",
                             executor_kind="manager")
        recovery.update_task_status("t-mgr", "completed")

    def tearDown(self) -> None:
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _request(self, path, *, authorized=True):
        req = Request(self.base + path)
        req.add_header("Origin", "http://localhost:1420")
        if authorized:
            req.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw) if raw else {}

    def test_summary_endpoint_authorized(self) -> None:
        status, payload = self._request(
            f"/api/agent-fabric/runs/{self.org}/summary?project_id={self.project}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], RUN_SUMMARY_SCHEMA)
        self.assertEqual(payload["task_count"], 1)
        self.assertEqual(payload["run_state"], "completed")

    def test_summary_foreign_nonexistent_equivalent(self) -> None:
        s1, b1 = self._request(f"/api/agent-fabric/runs/{self.org}/summary?project_id=wrong")
        s2, b2 = self._request("/api/agent-fabric/runs/no-such-org/summary?project_id=" + self.project)
        self.assertEqual(s1, 404); self.assertEqual(s2, 404); self.assertEqual(b1, b2)

    def test_tree_endpoint_still_works_via_generalized_route(self) -> None:
        status, payload = self._request(
            f"/api/agent-fabric/runs/{self.org}/tree?project_id={self.project}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], RUN_TREE_SCHEMA)

    def test_missing_project_400(self) -> None:
        status, payload = self._request(f"/api/agent-fabric/runs/{self.org}/summary")
        self.assertEqual(status, 400)
        self.assertIn("project_id", payload["error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
