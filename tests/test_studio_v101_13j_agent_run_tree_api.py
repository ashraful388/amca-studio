"""PS1-13J Agent Run-Tree API (bounded read-only backend API only).

Truthful, project-isolated reconstruction of org -> manager -> children ->
tasks -> actual model/tool bindings -> artifacts -> verification/terminal
from EXISTING 13H/13I + A2 persistence. No UI, no polling, no run-state
mutation, V110 unchanged, zero paid calls. FAKE-ONLY secret canaries.

All mutable test state under mandated scratch root
``D:\\ChatGPT\\AMCA\\.agent-tmp\\ps1-agent-13j``.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.agent_provenance import AgentProvenanceStore
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_run_tree import (
    MAX_DEPTH,
    MAX_NODES_PER_RESPONSE,
    RUN_TREE_SCHEMA,
    UnknownRunOrganization,
    build_run_tree,
)
from amca.service import create_server

MANDATED_SCRATCH = Path(r"D:\ChatGPT\AMCA\.agent-tmp\ps1-agent-13j")

FAKE_BEARER = "Bearer TEST-SECRET-123"
FAKE_API_KEY = "api_key=FAKE_ONLY"
FAKE_PASSWORD = "password=NOT_REAL"


class RunTreeStoreTests(unittest.TestCase):
    """Store-level tree construction (no HTTP)."""

    def setUp(self) -> None:
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="tree-", dir=MANDATED_SCRATCH))
        self.database = self.workdir / "amca.db"
        self.recovery = AgentRecoveryStore(self.database)
        self.provenance = AgentProvenanceStore(self.database)
        self.project = "p-13j"
        self.org = "o-13j"
        self.recovery.create_organization(
            organization_id=self.org, project_id=self.project,
            goal=f"bounded goal {FAKE_BEARER}", budget_usd=5.0, max_concurrent=4,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------

    def _task(self, task_id, *, agent_id=None, parent_agent=None, depth=0,
              status="completed", focus="bounded focus", depends_on=None,
              attempt=1):
        self.recovery.create_task(
            task_id=task_id, organization_id=self.org, project_id=self.project,
            agent_id=agent_id or f"a-{task_id}", focus=focus,
            executor_kind="manager" if depth == 0 else "nano_local_model",
            manager_depth=depth, proposed_by_manager_id=parent_agent,
            depends_on=depends_on or [],
        )
        self.recovery.update_task_status(task_id, status)
        return task_id

    def _model_binding(self, task_id, *, attempt=1, call_key="model:main",
                       model_id="qwen3:1.7b", provider="ollama", outcome="succeeded",
                       response_text=None):
        return self.provenance.record_model_binding(
            organization_id=self.org, project_id=self.project, task_id=task_id,
            task_attempt=attempt, call_key=call_key, agent_id=f"a-{task_id}",
            executor_kind="nano_local_model", adapter_id=f"adapter-{model_id}",
            provider_id=provider, model_id=model_id, is_local=True,
            outcome=outcome, response_text=response_text,
        )

    def _tool_binding(self, task_id, *, attempt=1, tool_id="artifact.export",
                      operation="export", outcome="succeeded",
                      result_ref=None, result_text=None):
        return self.provenance.record_tool_binding(
            organization_id=self.org, project_id=self.project, task_id=task_id,
            task_attempt=attempt, agent_id=f"a-{task_id}",
            executor_kind="nano_local_model", tool_id=tool_id,
            operation=operation, outcome=outcome,
            tool_execution_id=f"tx-{task_id}-{tool_id}-{attempt}",
            capability_ref="artifact.export", result_ref=result_ref,
            result_text=result_text,
        )

    def _tree(self, **kw):
        return build_run_tree(
            self.recovery, self.provenance, self.org,
            requesting_project_id=self.project, **kw,
        )

    @staticmethod
    def _walk(node):
        yield node
        for child in node["children"]:
            yield from RunTreeStoreTests._walk(child)

    def _all_nodes(self, tree):
        return [n for root in tree["roots"] for n in self._walk(root)]

    # -- 1. structure + hierarchy ----------------------------------------------

    def test_manager_child_hierarchy_reconstructs(self) -> None:
        self._task("mgr", depth=0, status="retired")
        self._task("kid-1", parent_agent="a-mgr", depth=1)
        self._task("kid-2", parent_agent="a-mgr", depth=1)
        tree = self._tree()
        self.assertEqual(len(tree["roots"]), 1)
        root = tree["roots"][0]
        self.assertEqual(root["task_id"], "mgr")
        self.assertEqual(sorted(c["task_id"] for c in root["children"]),
                         ["kid-1", "kid-2"])
        self.assertEqual(root["depth"], 0)
        self.assertEqual({c["depth"] for c in root["children"]}, {1})

    def test_deterministic_ordering_repeat_stable(self) -> None:
        self._task("mgr", depth=0)
        for name in ("c-3", "c-1", "c-2"):
            self._task(name, parent_agent="a-mgr", depth=1)
        first = json.dumps(self._tree(), sort_keys=True)
        second = json.dumps(self._tree(), sort_keys=True)
        self.assertEqual(first, second)
        kids = [n["task_id"] for n in self._tree()["roots"][0]["children"]]
        # Persisted creation sequence (not alphabetical) — truthful ordering.
        self.assertEqual(kids, ["c-3", "c-1", "c-2"])

    def test_orphan_without_parent_is_root_not_fabricated_parent(self) -> None:
        self._task("orphan", parent_agent="no-such-manager", depth=1)
        tree = self._tree()
        self.assertEqual([r["task_id"] for r in tree["roots"]], ["orphan"])

    # -- 2. lifecycle fidelity ---------------------------------------------------

    def test_lifecycle_states_pass_through_verbatim(self) -> None:
        states = ["proposed", "authorized", "queued", "starting", "running",
                  "waiting", "verifying", "completed", "merged", "retired",
                  "failed", "cancelled"]
        for index, state in enumerate(states):
            self._task(f"t-{index}", depth=0, status=state)
        tree = self._tree()
        self.assertEqual(tree["status_coverage"], sorted(set(states)))
        counts = tree["lifecycle_counts"]
        self.assertEqual(sum(counts.values()), len(states))
        self.assertNotIn("suspended", counts)  # no fictional states

    # -- 3. security ---------------------------------------------------------------

    def test_foreign_project_same_error_as_nonexistent(self) -> None:
        self._task("mgr", depth=0)
        with self.assertRaises(UnknownRunOrganization) as foreign:
            build_run_tree(self.recovery, self.provenance, self.org,
                           requesting_project_id="p-other")
        with self.assertRaises(UnknownRunOrganization) as missing:
            build_run_tree(self.recovery, self.provenance, "o-nope",
                           requesting_project_id=self.project)
        self.assertEqual(str(foreign.exception), str(missing.exception))

    def test_no_response_payload_or_raw_input_leak(self) -> None:
        self._task("mgr", depth=0)
        self.recovery.record_call_state(
            organization_id=self.org, task_id="mgr", task_attempt_number=1,
            call_key="model:main", state="RESPONSE_COMMITTED",
            executor_kind="manager", adapter_id="adapter-x",
            reservation_id="r-1",
            response_payload=f"raw reply {FAKE_BEARER} {FAKE_API_KEY}",
        )
        blob = json.dumps(self._tree())
        self.assertNotIn("TEST-SECRET-123", blob)
        self.assertNotIn("FAKE_ONLY", blob)
        self.assertNotIn('"response_payload"', blob)
        self.assertNotIn('"input_payload"', blob)
        node = self._all_nodes(self._tree())[0]
        self.assertTrue(node["call_attempts"][0]["response_payload_present"])

    def test_binding_previews_redacted(self) -> None:
        self._task("mgr", depth=0)
        self._model_binding("mgr", response_text=f"reply {FAKE_BEARER}")
        self._tool_binding("mgr", result_ref="art-1", result_text=FAKE_PASSWORD)
        blob = json.dumps(self._tree())
        self.assertNotIn("TEST-SECRET-123", blob)
        self.assertNotIn("NOT_REAL", blob)

    def test_organization_goal_redacted(self) -> None:
        blob = json.dumps(self._tree())
        self.assertNotIn("TEST-SECRET-123", blob)
        self.assertIn("goal", blob)  # field present, value sanitized

    # -- 4. bindings + multi-call + failure/cancel -------------------------------

    def test_model_and_tool_bindings_attached(self) -> None:
        self._task("mgr", depth=0)
        self._model_binding("mgr")
        self._tool_binding("mgr", result_ref="art-77")
        node = self._all_nodes(self._tree())[0]
        self.assertEqual(node["model_bindings"][0]["model_id"], "qwen3:1.7b")
        self.assertEqual(node["model_bindings"][0]["locality"], "local")
        self.assertEqual(node["tool_bindings"][0]["tool_id"], "artifact.export")
        self.assertEqual(node["artifact_refs"], ["art-77"])

    def test_multi_call_per_task(self) -> None:
        self._task("mgr", depth=0)
        self._model_binding("mgr", call_key="model:main")
        self._model_binding("mgr", call_key="model:fallback", model_id="llama3.2:3b")
        node = self._all_nodes(self._tree())[0]
        keys = sorted(b["call_key"] for b in node["model_bindings"])
        self.assertEqual(keys, ["model:fallback", "model:main"])

    def test_cancelled_task_no_fabricated_bindings(self) -> None:
        self._task("mgr", depth=0)
        self._task("kid", parent_agent="a-mgr", depth=1, status="cancelled")
        nodes = {n["task_id"]: n for n in self._all_nodes(self._tree())}
        self.assertEqual(nodes["kid"]["model_bindings"], [])
        self.assertEqual(nodes["kid"]["tool_bindings"], [])
        self.assertEqual(nodes["kid"]["status"], "cancelled")

    def test_failed_task_error_sanitized(self) -> None:
        self._task("mgr", depth=0, status="failed")
        self.recovery.update_task_status(
            "mgr", "failed", error=f"ValueError: boom {FAKE_API_KEY}"
        )
        node = self._all_nodes(self._tree())[0]
        self.assertNotIn("FAKE_ONLY", json.dumps(node["result"]))
        self.assertIsNotNone(node["result"]["error"])

    # -- 5. restart durability ------------------------------------------------------

    def test_tree_survives_store_reopen(self) -> None:
        self._task("mgr", depth=0)
        self._model_binding("mgr")
        self._tool_binding("mgr", result_ref="art-9")
        before = json.dumps(self._tree(), sort_keys=True)
        recovery2 = AgentRecoveryStore(self.database)
        provenance2 = AgentProvenanceStore(self.database)
        after = json.dumps(
            build_run_tree(recovery2, provenance2, self.org,
                           requesting_project_id=self.project),
            sort_keys=True,
        )
        self.assertEqual(before, after)

    # -- 6. bounds -------------------------------------------------------------------

    def test_node_budget_truncation_metadata(self) -> None:
        self._task("mgr", depth=0)
        for index in range(6):
            self._task(f"kid-{index}", parent_agent="a-mgr", depth=1)
        tree = self._tree(max_nodes=3)
        self.assertTrue(tree["truncated"])
        self.assertLessEqual(tree["included_nodes"], 3)
        self.assertGreater(tree["omitted_nodes"], 0)
        self.assertEqual(tree["included_nodes"] + tree["omitted_nodes"], 7)
        self.assertTrue(tree["continuation"]["supported"])
        self.assertLessEqual(len(self._all_nodes(tree)), 3)

    def test_depth_cap(self) -> None:
        parent = "L0"
        self._task(parent, depth=0)
        for level in range(1, 6):
            self._task(f"L{level}", parent_agent=f"a-L{level - 1}", depth=level)
            parent = f"L{level}"
        tree = self._tree(max_depth=2)
        self.assertTrue(tree["truncated"])
        depths = {n["depth"] for n in self._all_nodes(tree)}
        self.assertLessEqual(max(depths), 2)

    def test_defaults_match_contract(self) -> None:
        self.assertEqual(MAX_NODES_PER_RESPONSE, 250)
        self.assertEqual(MAX_DEPTH, 8)
        tree = self._tree()
        self.assertEqual(tree["bounds"], {"max_nodes_per_response": 250, "max_depth": 8})
        self.assertEqual(tree["schema"], RUN_TREE_SCHEMA)

    # -- 7. verdicts / verification stage ------------------------------------------

    def test_verifications_exposed(self) -> None:
        self._task("mgr", depth=0)
        self.recovery.record_verdict(
            organization_id=self.org, task_id="mgr", task_attempt_number=1,
            verifier_id="v-1", verdict="verified", summary="looks good",
            evidence_refs_json=["ev-1", "ev-2"],
        )
        node = self._all_nodes(self._tree())[0]
        verdict = node["verifications"][0]
        self.assertEqual(verdict["verifier_id"], "v-1")
        self.assertEqual(verdict["evidence_refs"], ["ev-1", "ev-2"])


class RunTreeHttpRouteTests(unittest.TestCase):
    """Endpoint behavior on the real service loopback (same harness as
    runtime hardening): authorized read 200; foreign == nonexistent 404
    identical body; missing project 400."""

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
        session = self._request("/api/session", authorized=False)[1]
        self.token = str(session["session_token"])
        self.recovery = AgentRecoveryStore(self.database)
        self.project = "p-13j-http"
        self.org = "o-13j-http"
        self.recovery.create_organization(
            organization_id=self.org, project_id=self.project,
            goal="http bounded goal", budget_usd=1.0, max_concurrent=2,
        )
        self.recovery.create_task(
            task_id="t-http-mgr", organization_id=self.org,
            project_id=self.project, agent_id="a-mgr", focus="http mgr",
            executor_kind="manager",
        )
        self.recovery.update_task_status("t-http-mgr", "completed")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
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

    def test_endpoint_authorized_returns_tree(self) -> None:
        status, payload = self._request(
            f"/api/agent-fabric/runs/{self.org}/tree?project_id={self.project}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], RUN_TREE_SCHEMA)
        self.assertEqual(payload["roots"][0]["task_id"], "t-http-mgr")

    def test_foreign_and_nonexistent_indistinguishable(self) -> None:
        s1, b1 = self._request(
            f"/api/agent-fabric/runs/{self.org}/tree?project_id=wrong-proj"
        )
        s2, b2 = self._request(
            "/api/agent-fabric/runs/no-such-org/tree?project_id=" + self.project
        )
        self.assertEqual(s1, 404)
        self.assertEqual(s2, 404)
        self.assertEqual(b1, b2)  # zero existence disclosure

    def test_missing_project_id_bad_request(self) -> None:
        status, payload = self._request(
            f"/api/agent-fabric/runs/{self.org}/tree"
        )
        self.assertEqual(status, 400)
        self.assertIn("project_id", payload["error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
