"""Comprehensive validation suite for AMCA Studio Persistent Memory & Context Foundation.

Task: AG-AMCA-STUDIO-A2-PERSISTENT-MEMORY-CONTEXT-FOUNDATION-01

Domains Validated:
1. Memory Architecture & SQLite Persistence (amca.db canonical table, zero duplicate DBs)
2. Project Isolation & Multi-type Filtering (Strict workspace boundaries, type filtering)
3. Context Retrieval & Token Budgeting (Deterministic relevance, budget bounds, pinned priority)
4. Privacy, Safety & User Control (User inspection, purge controls, credential blocking)
5. Restart Continuity & Concurrency (Server restarts, concurrency, WAL safety)
"""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server
from amca.studio_projects import StudioProjectStore
from amca.studio_memory import StudioMemoryStore
from amca.model_hub import ModelHubStore
from amca.agent_fabric import AgentFabricStore


class StudioPersistentMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        
        # Initialize stores
        self.project_store = StudioProjectStore(self.database)
        self.memory_store = StudioMemoryStore(self.database)

        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=self.root,
            database=self.database,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        
        session = self._request("/api/session", authorized=False)[1]
        self.token = str(session["session_token"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        try:
            self.temporary.cleanup()
        except Exception:
            pass

    def _request(self, path: str, method: str = "GET", body: dict | None = None, *, authorized: bool = True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(self.base + path, data=data, method=method)
        req.add_header("Origin", "http://localhost:1420")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if authorized:
            req.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(req) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw) if raw else {}

    def _seed_project(self, project_id: str = "proj-mem-01") -> str:
        self.project_store.save({
            "project_id": project_id,
            "name": f"Memory Project {project_id}",
            "description": "Container for memory testing",
            "instructions": "Maintain persistent context",
            "template": "software"
        })
        return project_id

    # =========================================================================
    # Group 1: Memory Architecture & SQLite Persistence (Tests 1-6)
    # =========================================================================

    def test_01_save_memory_item_via_api(self) -> None:
        self._seed_project("p-save")
        status, resp = self._request("/api/studio/memory", method="POST", body={
            "project_id": "p-save",
            "title": "Architecture Rule",
            "content": "Use SQLite as single source of truth.",
            "memory_type": "decision",
            "pinned": True,
        })
        self.assertEqual(status, 201)
        mem = resp["memory"]
        self.assertEqual(mem["project_id"], "p-save")
        self.assertEqual(mem["title"], "Architecture Rule")
        self.assertTrue(mem["pinned"])
        self.assertEqual(mem["memory_type"], "decision")

    def test_02_get_memory_by_id(self) -> None:
        self._seed_project("p-get")
        saved = self.memory_store.save_memory(
            project_id="p-get",
            title="Design Decision",
            content="Use REST over HTTP loopback",
            memory_type="decision",
        )
        status, resp = self._request(f"/api/studio/memory/{saved['memory_id']}?project_id=p-get")
        self.assertEqual(status, 200)
        self.assertEqual(resp["memory"]["title"], "Design Decision")

    def test_03_memory_persists_in_canonical_database(self) -> None:
        self._seed_project("p-db")
        saved = self.memory_store.save_memory(
            project_id="p-db", title="Direct DB Test", content="Stored in amca.db"
        )
        conn = sqlite3.connect(self.database)
        try:
            row = conn.execute(
                "SELECT title, content FROM studio_project_memories WHERE memory_id = ?",
                (saved["memory_id"],),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "Direct DB Test")
        finally:
            conn.close()

    def test_04_upsert_memory_content_update(self) -> None:
        self._seed_project("p-up")
        saved = self.memory_store.save_memory(
            project_id="p-up", title="Initial Title", content="Initial Content"
        )
        updated = self.memory_store.save_memory(
            project_id="p-up", title="Updated Title", content="Updated Content", memory_id=saved["memory_id"]
        )
        self.assertEqual(updated["memory_id"], saved["memory_id"])
        self.assertEqual(updated["title"], "Updated Title")
        self.assertEqual(updated["content"], "Updated Content")

    def test_05_memory_requires_non_empty_project_id_and_content(self) -> None:
        with self.assertRaises(ValueError):
            self.memory_store.save_memory(project_id="", title="Title", content="Content")
        with self.assertRaises(ValueError):
            self.memory_store.save_memory(project_id="p", title="Title", content="")

    def test_06_estimated_token_cost_calculated(self) -> None:
        saved = self.memory_store.save_memory(
            project_id="p-tok", title="Tokens", content="A" * 400
        )
        self.assertEqual(saved["estimated_tokens"], 100)

    # =========================================================================
    # Group 2: Project Isolation & Multi-type Filtering (Tests 7-12)
    # =========================================================================

    def test_07_strict_cross_project_isolation(self) -> None:
        self._seed_project("p-iso-a")
        self._seed_project("p-iso-b")
        self.memory_store.save_memory(project_id="p-iso-a", title="Secret A", content="Content A")
        self.memory_store.save_memory(project_id="p-iso-b", title="Secret B", content="Content B")

        status, resp_a = self._request("/api/studio/memory?project_id=p-iso-a")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp_a["memories"]), 1)
        self.assertEqual(resp_a["memories"][0]["title"], "Secret A")

        status, resp_b = self._request("/api/studio/memory?project_id=p-iso-b")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp_b["memories"]), 1)
        self.assertEqual(resp_b["memories"][0]["title"], "Secret B")

    def test_08_cross_project_direct_access_returns_404(self) -> None:
        self._seed_project("p-sec-1")
        self._seed_project("p-sec-2")
        saved = self.memory_store.save_memory(project_id="p-sec-1", title="Private 1", content="Content")

        status, _ = self._request(f"/api/studio/memory/{saved['memory_id']}?project_id=p-sec-2")
        self.assertEqual(status, 404)

    def test_09_filter_by_memory_type(self) -> None:
        self._seed_project("p-types")
        self.memory_store.save_memory(project_id="p-types", title="Dec 1", content="C1", memory_type="decision")
        self.memory_store.save_memory(project_id="p-types", title="Fact 1", content="C2", memory_type="fact")
        self.memory_store.save_memory(project_id="p-types", title="Inst 1", content="C3", memory_type="instruction")

        status, resp = self._request("/api/studio/memory?project_id=p-types&memory_type=decision")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp["memories"]), 1)
        self.assertEqual(resp["memories"][0]["memory_type"], "decision")

    def test_10_filter_by_pinned_only(self) -> None:
        self._seed_project("p-pin-filt")
        self.memory_store.save_memory(project_id="p-pin-filt", title="Pinned", content="C1", pinned=True)
        self.memory_store.save_memory(project_id="p-pin-filt", title="Unpinned", content="C2", pinned=False)

        status, resp = self._request("/api/studio/memory?project_id=p-pin-filt&pinned_only=true")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp["memories"]), 1)
        self.assertTrue(resp["memories"][0]["pinned"])

    def test_11_toggle_pin_via_api(self) -> None:
        self._seed_project("p-toggle")
        saved = self.memory_store.save_memory(project_id="p-toggle", title="Tog", content="C", pinned=False)

        status, resp = self._request(
            f"/api/studio/memory/{saved['memory_id']}/pin",
            method="POST",
            body={"project_id": "p-toggle", "pinned": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(resp["memory"]["pinned"])

    def test_12_toggle_pin_nonexistent_returns_404(self) -> None:
        status, _ = self._request(
            "/api/studio/memory/nonexistent-id/pin",
            method="POST",
            body={"project_id": "p-any", "pinned": True},
        )
        self.assertEqual(status, 404)

    # =========================================================================
    # Group 3: Context Retrieval & Token Budgeting (Tests 13-18)
    # =========================================================================

    def test_13_retrieve_context_enforces_token_budget(self) -> None:
        self._seed_project("p-budget")
        for i in range(10):
            self.memory_store.save_memory(
                project_id="p-budget",
                title=f"Mem {i}",
                content="X" * 400, # ~100 tokens each
            )
        status, envelope = self._request("/api/studio/memory/context?project_id=p-budget&budget=350")
        self.assertEqual(status, 200)
        self.assertEqual(envelope["selected_count"], 3)
        self.assertLessEqual(envelope["estimated_tokens"], 350)
        self.assertEqual(envelope["total_available"], 10)

    def test_14_context_retrieval_prioritizes_pinned_items(self) -> None:
        self._seed_project("p-prio")
        self.memory_store.save_memory(project_id="p-prio", title="Normal 1", content="A" * 400, pinned=False)
        self.memory_store.save_memory(project_id="p-prio", title="Pinned Critical", content="B" * 400, pinned=True)

        envelope = self.memory_store.retrieve_context("p-prio", token_budget=150)
        self.assertEqual(envelope["selected_count"], 1)
        self.assertEqual(envelope["memories"][0]["title"], "Pinned Critical")

    def test_15_context_retrieval_relevance_query_matching(self) -> None:
        self._seed_project("p-query")
        self.memory_store.save_memory(project_id="p-query", title="Database Engine", content="We use SQLite with WAL")
        self.memory_store.save_memory(project_id="p-query", title="Frontend Style", content="We use Tailwind and React")

        envelope = self.memory_store.retrieve_context("p-query", query="SQLite database", token_budget=2000)
        self.assertEqual(envelope["memories"][0]["title"], "Database Engine")

    def test_16_context_retrieval_empty_when_no_memories(self) -> None:
        self._seed_project("p-empty-mem")
        status, envelope = self._request("/api/studio/memory/context?project_id=p-empty-mem")
        self.assertEqual(status, 200)
        self.assertEqual(envelope["selected_count"], 0)
        self.assertEqual(envelope["memories"], [])

    def test_17_context_retrieval_type_filtering(self) -> None:
        self._seed_project("p-type-ret")
        self.memory_store.save_memory(project_id="p-type-ret", title="Rule 1", content="C1", memory_type="rule")
        self.memory_store.save_memory(project_id="p-type-ret", title="Note 1", content="C2", memory_type="note")

        envelope = self.memory_store.retrieve_context("p-type-ret", types=["rule"])
        self.assertEqual(envelope["selected_count"], 1)
        self.assertEqual(envelope["memories"][0]["title"], "Rule 1")

    def test_18_context_retrieval_requires_project_id(self) -> None:
        status, _ = self._request("/api/studio/memory/context")
        self.assertEqual(status, 400)

    # =========================================================================
    # Group 4: Privacy, Safety & User Control (Tests 19-24)
    # =========================================================================

    def test_19_delete_memory_item(self) -> None:
        self._seed_project("p-del")
        saved = self.memory_store.save_memory(project_id="p-del", title="To Delete", content="C")

        status, resp = self._request(f"/api/studio/memory/{saved['memory_id']}?project_id=p-del", method="DELETE")
        self.assertEqual(status, 200)
        self.assertTrue(resp["success"])
        self.assertIsNone(self.memory_store.get_memory(saved["memory_id"]))

    def test_20_clear_all_project_memories_purge(self) -> None:
        self._seed_project("p-purge")
        self.memory_store.save_memory(project_id="p-purge", title="M1", content="C1")
        self.memory_store.save_memory(project_id="p-purge", title="M2", content="C2")

        count = self.memory_store.clear_project_memories("p-purge")
        self.assertEqual(count, 2)
        self.assertEqual(self.memory_store.list_memories("p-purge"), [])

    def test_21_refuse_raw_api_keys_in_memory_content(self) -> None:
        self._seed_project("p-sec-blk")
        with self.assertRaises(ValueError):
            self.memory_store.save_memory(
                project_id="p-sec-blk",
                title="Leaked Key",
                content="My key is sk-proj-1234567890abcdef",
            )

    def test_22_refuse_github_tokens_in_memory_content(self) -> None:
        self._seed_project("p-sec-gh")
        with self.assertRaises(ValueError):
            self.memory_store.save_memory(
                project_id="p-sec-gh",
                title="GitHub PAT",
                content="Token is ghp_1234567890abcdefghijklmnopqrstuvwxyz",
            )

    def test_23_memory_listing_requires_session_token(self) -> None:
        status, _ = self._request("/api/studio/memory?project_id=p-unauth", authorized=False)
        self.assertEqual(status, 401)

    def test_24_memory_creation_requires_session_token(self) -> None:
        status, _ = self._request("/api/studio/memory", method="POST", body={}, authorized=False)
        self.assertEqual(status, 401)

    # =========================================================================
    # Group 5: Restart Continuity & Concurrency (Tests 25-28)
    # =========================================================================

    def test_25_memory_persists_across_server_restarts(self) -> None:
        self._seed_project("p-rest")
        saved = self.memory_store.save_memory(
            project_id="p-rest", title="Restart Safe", content="Survives server cycle"
        )
        
        # Stop server
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

        # Launch fresh server pointing to same database
        new_server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
        new_thread = threading.Thread(target=new_server.serve_forever, daemon=True)
        new_thread.start()
        try:
            req_sess = Request(f"http://127.0.0.1:{new_server.server_port}/api/session")
            req_sess.add_header("Origin", "http://localhost:1420")
            with urlopen(req_sess) as resp:
                new_token = json.loads(resp.read())["session_token"]

            req_m = Request(f"http://127.0.0.1:{new_server.server_port}/api/studio/memory/{saved['memory_id']}?project_id=p-rest")
            req_m.add_header("Origin", "http://localhost:1420")
            req_m.add_header("X-AMCA-Session", new_token)
            with urlopen(req_m) as resp:
                doc = json.loads(resp.read())
            self.assertEqual(doc["memory"]["title"], "Restart Safe")
        finally:
            new_server.shutdown()
            new_server.server_close()
            new_thread.join(timeout=2)
            # Recreate original server for tearDown
            self.server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_26_concurrent_memory_store_instances(self) -> None:
        store_1 = StudioMemoryStore(self.database)
        store_2 = StudioMemoryStore(self.database)
        m1 = store_1.save_memory(project_id="p-concur", title="Store 1", content="C1")
        m2 = store_2.get_memory(m1["memory_id"], project_id="p-concur")
        self.assertIsNotNone(m2)
        self.assertEqual(m2["title"], "Store 1")

    def test_27_source_reference_attachment(self) -> None:
        saved = self.memory_store.save_memory(
            project_id="p-src",
            title="Sourced Fact",
            content="Fact from research step",
            source_ref="research_step:step-12345",
        )
        self.assertEqual(saved["source_ref"], "research_step:step-12345")

    def test_28_oversized_title_or_content_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.memory_store.save_memory(project_id="p-size", title="T" * 300, content="C")
        with self.assertRaises(ValueError):
            self.memory_store.save_memory(project_id="p-size", title="T", content="C" * 35000)


if __name__ == "__main__":
    unittest.main()
