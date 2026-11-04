"""Validation suite for AMCA Studio Artifact Knowledge & Workspace Foundation.

Task: AG-AMCA-STUDIO-A3-ARTIFACT-KNOWLEDGE-WORKSPACE-FOUNDATION-01

Domains Validated:
1. Artifact Storage & Canonical Persistence (Immutable versioning, SHA256 integrity, provenance)
2. Project Isolation & Workspace Partitioning (Strict project-scoped reads, 404 on cross-project probe)
3. Artifact Center API Surface (Listing, version histories, provenance metadata)
4. Memory <-> Artifact Knowledge Linking (User-driven linking, source_ref tracing, pinned knowledge)
5. Continuity, Concurrency & Restart Safety (Server restarts, concurrent store instances)
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
from amca.artifacts import ArtifactStore, ArtifactError


class StudioArtifactKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        
        # Initialize stores
        self.project_store = StudioProjectStore(self.database)
        self.memory_store = StudioMemoryStore(self.database)
        self.artifact_store = ArtifactStore(
            database=self.database.parent / "artifacts.sqlite3",
            payloads_root=self.database.parent / "artifact-payloads",
        )

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

    def _seed_project(self, project_id: str = "proj-art-01") -> str:
        self.project_store.save({
            "project_id": project_id,
            "name": f"Artifact Project {project_id}",
            "description": "Container for artifact testing",
            "instructions": "Manage project artifacts cleanly",
            "template": "software"
        })
        return project_id

    # =========================================================================
    # Group 1: Artifact Storage & Canonical Persistence (Tests 1-6)
    # =========================================================================

    def test_01_author_new_artifact_via_api(self) -> None:
        self._seed_project("p-art-author")
        status, resp = self._request("/api/projects/p-art-author/artifacts", method="POST", body={
            "artifact_type": "markdown",
            "title": "Architecture Blueprint",
            "content": "# Blueprint\nCanonical architecture notes.",
            "created_by": "lead-architect",
        })
        self.assertEqual(status, 201)
        art = resp["artifact"]
        self.assertEqual(art["title"], "Architecture Blueprint")
        self.assertEqual(art["artifact_type"], "markdown")
        self.assertEqual(art["current_version"], 1)

    def test_02_author_next_version_immutable_append(self) -> None:
        self._seed_project("p-art-ver")
        created = self.artifact_store.author(
            project_id="p-art-ver",
            artifact_type="markdown",
            title="Design Spec",
            content="# V1 Content",
            created_by="user",
        )
        art_id = created["artifact_id"]

        updated = self.artifact_store.edit(
            artifact_id=art_id,
            content="# V2 Content",
            created_by="user",
            base_version=1,
            reason="Update spec",
        )
        self.assertEqual(updated["current_version"], 2)
        v1_src = self.artifact_store.read_source(art_id, version=1)["content"]
        v2_src = self.artifact_store.read_source(art_id, version=2)["content"]
        self.assertEqual(v1_src, "# V1 Content")
        self.assertEqual(v2_src, "# V2 Content")

    def test_03_restore_version_creates_new_version(self) -> None:
        self._seed_project("p-art-rest")
        created = self.artifact_store.author(
            project_id="p-art-rest", artifact_type="markdown", title="Doc", content="Ver 1", created_by="u"
        )
        art_id = created["artifact_id"]
        self.artifact_store.edit(
            artifact_id=art_id, content="Ver 2", created_by="u", base_version=1
        )
        restored = self.artifact_store.restore(
            artifact_id=art_id, version=1, created_by="u"
        )
        self.assertEqual(restored["current_version"], 3)
        self.assertEqual(self.artifact_store.read_source(art_id, version=3)["content"], "Ver 1")

    def test_04_artifact_sha256_integrity_check(self) -> None:
        created = self.artifact_store.author(
            project_id="p-sha", artifact_type="markdown", title="Doc", content="Integrity Content", created_by="u"
        )
        versions = self.artifact_store.get(created["artifact_id"])["versions"]
        self.assertEqual(len(versions), 1)
        self.assertTrue(len(versions[0]["payload_sha256"]) == 64)

    def test_05_oversized_source_content_rejected(self) -> None:
        huge = "X" * (5 * 1024 * 1024)
        with self.assertRaises(ArtifactError):
            self.artifact_store.author(
                project_id="p-huge", artifact_type="markdown", title="Huge", content=huge, created_by="u"
            )

    def test_06_unsupported_artifact_type_rejected(self) -> None:
        with self.assertRaises(ArtifactError):
            self.artifact_store.author(
                project_id="p-bad-type", artifact_type="unsupported_type_xyz", title="Bad", content="Content", created_by="u"
            )

    # =========================================================================
    # Group 2: Project Isolation & Workspace Partitioning (Tests 7-12)
    # =========================================================================

    def test_07_artifacts_strictly_isolated_by_project(self) -> None:
        self._seed_project("p-iso-1")
        self._seed_project("p-iso-2")
        self.artifact_store.author(
            project_id="p-iso-1", artifact_type="markdown", title="Doc 1", content="C1", created_by="u"
        )
        self.artifact_store.author(
            project_id="p-iso-2", artifact_type="markdown", title="Doc 2", content="C2", created_by="u"
        )

        status, resp_1 = self._request("/api/studio/artifacts?project_id=p-iso-1")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp_1["artifacts"]), 1)
        self.assertEqual(resp_1["artifacts"][0]["title"], "Doc 1")

        status, resp_2 = self._request("/api/studio/artifacts?project_id=p-iso-2")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp_2["artifacts"]), 1)
        self.assertEqual(resp_2["artifacts"][0]["title"], "Doc 2")

    def test_08_cross_project_artifact_detail_returns_404(self) -> None:
        self._seed_project("p-det-a")
        self._seed_project("p-det-b")
        created = self.artifact_store.author(
            project_id="p-det-a", artifact_type="markdown", title="Secret A", content="C", created_by="u"
        )
        status, _ = self._request(f"/api/studio/artifacts/{created['artifact_id']}?project_id=p-det-b")
        self.assertEqual(status, 404)

    def test_09_artifacts_listing_requires_project_id(self) -> None:
        status, _ = self._request("/api/studio/artifacts")
        self.assertEqual(status, 400)

    def test_10_artifact_detail_requires_project_id(self) -> None:
        status, _ = self._request("/api/studio/artifacts/some-artifact-id")
        self.assertEqual(status, 400)

    def test_11_empty_project_returns_empty_artifact_list(self) -> None:
        self._seed_project("p-empty-art")
        status, resp = self._request("/api/studio/artifacts?project_id=p-empty-art")
        self.assertEqual(status, 200)
        self.assertEqual(resp["artifacts"], [])

    def test_12_nonexistent_artifact_returns_404(self) -> None:
        self._seed_project("p-exist")
        status, _ = self._request("/api/studio/artifacts/nonexistent-art-999?project_id=p-exist")
        self.assertEqual(status, 404)

    # =========================================================================
    # Group 3: Artifact Center API Surface (Tests 13-18)
    # =========================================================================

    def test_13_artifact_center_detail_endpoint_contract(self) -> None:
        self._seed_project("p-art-det")
        created = self.artifact_store.author(
            project_id="p-art-det",
            artifact_type="markdown",
            title="Design Specification",
            content="# Spec V1",
            created_by="designer",
        )
        status, detail = self._request(f"/api/studio/artifacts/{created['artifact_id']}?project_id=p-art-det")
        self.assertEqual(status, 200)
        self.assertIn("artifact", detail)
        self.assertIn("versions", detail)
        self.assertEqual(detail["versions_count"], 1)
        self.assertEqual(detail["artifact"]["title"], "Design Specification")

    def test_14_artifact_version_history_enumeration(self) -> None:
        self._seed_project("p-ver-hist")
        created = self.artifact_store.author(
            project_id="p-ver-hist", artifact_type="markdown", title="Iterative Doc", content="V1", created_by="u"
        )
        art_id = created["artifact_id"]
        self.artifact_store.edit(
            artifact_id=art_id, content="V2", created_by="u", base_version=1
        )
        self.artifact_store.edit(
            artifact_id=art_id, content="V3", created_by="u", base_version=2
        )

        status, detail = self._request(f"/api/studio/artifacts/{art_id}?project_id=p-ver-hist")
        self.assertEqual(status, 200)
        self.assertEqual(detail["versions_count"], 3)
        self.assertEqual(len(detail["versions"]), 3)

    def test_15_artifact_read_source_endpoint(self) -> None:
        self._seed_project("p-read-src")
        created = self.artifact_store.author(
            project_id="p-read-src", artifact_type="html", title="Report", content="<h1>Report</h1>", created_by="u"
        )
        art_id = created["artifact_id"]
        status, resp = self._request(f"/api/projects/p-read-src/artifacts/{art_id}/source")
        self.assertEqual(status, 200)
        self.assertEqual(resp["source"]["content"], "<h1>Report</h1>")

    def test_16_artifact_list_includes_memory_links_count(self) -> None:
        self._seed_project("p-links-count")
        created = self.artifact_store.author(
            project_id="p-links-count", artifact_type="markdown", title="Linked Doc", content="Content", created_by="u"
        )
        status, resp = self._request("/api/studio/artifacts?project_id=p-links-count")
        self.assertEqual(status, 200)
        self.assertEqual(resp["artifacts"][0]["memory_links_count"], 0)

    def test_17_artifact_created_by_provenance_truthful(self) -> None:
        created = self.artifact_store.author(
            project_id="p-prov", artifact_type="markdown", title="Doc", content="C", created_by="agent-researcher"
        )
        self.assertEqual(created["created_by"], "agent-researcher")

    def test_18_artifact_center_read_only_protection(self) -> None:
        status, _ = self._request("/api/studio/artifacts", method="POST", body={})
        self.assertIn(status, (400, 404, 405))

    # =========================================================================
    # Group 4: Memory <-> Artifact Knowledge Linking (Tests 19-24)
    # =========================================================================

    def test_19_link_artifact_to_memory_via_api(self) -> None:
        self._seed_project("p-link-art")
        created = self.artifact_store.author(
            project_id="p-link-art", artifact_type="markdown", title="API Spec", content="# API Contract", created_by="dev"
        )
        art_id = created["artifact_id"]

        status, resp = self._request(f"/api/studio/artifacts/{art_id}/link-memory", method="POST", body={
            "project_id": "p-link-art",
            "pinned": True,
        })
        self.assertEqual(status, 201)
        mem = resp["memory"]
        self.assertEqual(mem["source_ref"], f"artifact:{art_id}#v1")
        self.assertTrue(mem["pinned"])
        self.assertIn("# API Contract", mem["content"])

    def test_20_linked_memory_updates_artifact_listing_count(self) -> None:
        self._seed_project("p-art-cnt")
        created = self.artifact_store.author(
            project_id="p-art-cnt", artifact_type="markdown", title="Doc", content="C", created_by="u"
        )
        art_id = created["artifact_id"]

        self._request(f"/api/studio/artifacts/{art_id}/link-memory", method="POST", body={"project_id": "p-art-cnt"})
        
        status, resp = self._request("/api/studio/artifacts?project_id=p-art-cnt")
        self.assertEqual(resp["artifacts"][0]["memory_links_count"], 1)

    def test_21_linked_artifact_memory_visible_on_artifact_detail(self) -> None:
        self._seed_project("p-art-mem-vis")
        created = self.artifact_store.author(
            project_id="p-art-mem-vis", artifact_type="markdown", title="Target Doc", content="C", created_by="u"
        )
        art_id = created["artifact_id"]
        self._request(f"/api/studio/artifacts/{art_id}/link-memory", method="POST", body={"project_id": "p-art-mem-vis", "title": "Pinned Fact"})

        status, detail = self._request(f"/api/studio/artifacts/{art_id}?project_id=p-art-mem-vis")
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["linked_memories"]), 1)
        self.assertEqual(detail["linked_memories"][0]["title"], "Pinned Fact")

    def test_22_pinned_artifact_memory_included_in_context_retrieval(self) -> None:
        self._seed_project("p-art-ctx")
        created = self.artifact_store.author(
            project_id="p-art-ctx", artifact_type="markdown", title="Key Spec", content="Critical requirements", created_by="u"
        )
        self._request(f"/api/studio/artifacts/{created['artifact_id']}/link-memory", method="POST", body={
            "project_id": "p-art-ctx", "pinned": True
        })

        status, ctx = self._request("/api/studio/memory/context?project_id=p-art-ctx&budget=500")
        self.assertEqual(status, 200)
        self.assertEqual(ctx["selected_count"], 1)
        self.assertEqual(ctx["memories"][0]["source_ref"], f"artifact:{created['artifact_id']}#v1")

    def test_23_link_artifact_memory_cross_project_rejected(self) -> None:
        self._seed_project("p-lk-a")
        self._seed_project("p-lk-b")
        created = self.artifact_store.author(
            project_id="p-lk-a", artifact_type="markdown", title="Doc A", content="C", created_by="u"
        )
        status, _ = self._request(f"/api/studio/artifacts/{created['artifact_id']}/link-memory", method="POST", body={
            "project_id": "p-lk-b"
        })
        self.assertEqual(status, 400)

    def test_24_link_artifact_requires_session_token(self) -> None:
        status, _ = self._request("/api/studio/artifacts/art-1/link-memory", method="POST", body={}, authorized=False)
        self.assertEqual(status, 401)

    # =========================================================================
    # Group 5: Continuity, Concurrency & Restart Safety (Tests 25-28)
    # =========================================================================

    def test_25_artifact_persists_across_server_restarts(self) -> None:
        self._seed_project("p-art-rest-srv")
        created = self.artifact_store.author(
            project_id="p-art-rest-srv", artifact_type="markdown", title="Restart Doc", content="Survives restart", created_by="u"
        )
        art_id = created["artifact_id"]

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

            req_a = Request(f"http://127.0.0.1:{new_server.server_port}/api/studio/artifacts/{art_id}?project_id=p-art-rest-srv")
            req_a.add_header("Origin", "http://localhost:1420")
            req_a.add_header("X-AMCA-Session", new_token)
            with urlopen(req_a) as resp:
                doc = json.loads(resp.read())
            self.assertEqual(doc["artifact"]["title"], "Restart Doc")
        finally:
            new_server.shutdown()
            new_server.server_close()
            new_thread.join(timeout=2)
            # Recreate original server for tearDown
            self.server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_26_concurrent_artifact_store_instances(self) -> None:
        store1 = ArtifactStore(self.database.parent / "artifacts.sqlite3", self.database.parent / "artifact-payloads")
        store2 = ArtifactStore(self.database.parent / "artifacts.sqlite3", self.database.parent / "artifact-payloads")
        created = store1.author(
            project_id="p-concur", artifact_type="markdown", title="Concurrent Doc", content="C", created_by="u"
        )
        fetched = store2.get(created["artifact_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["title"], "Concurrent Doc")

    def test_27_export_supported_formats(self) -> None:
        self._seed_project("p-exp")
        created = self.artifact_store.author(
            project_id="p-exp", artifact_type="markdown", title="To Export", content="# Export Title", created_by="u"
        )
        status, exp = self._request(f"/api/projects/p-exp/artifacts/{created['artifact_id']}/export", method="POST", body={"format": "markdown"})
        self.assertEqual(status, 200)
        self.assertIn("export", exp)

    def test_28_security_no_secrets_in_artifact_listing(self) -> None:
        self._seed_project("p-sec-art")
        self.artifact_store.author(
            project_id="p-sec-art", artifact_type="markdown", title="Safe Doc", content="Plain text", created_by="u"
        )
        status, resp = self._request("/api/studio/artifacts?project_id=p-sec-art")
        dump = json.dumps(resp).casefold()
        self.assertNotIn("session_token", dump)
        self.assertNotIn("password", dump)


if __name__ == "__main__":
    unittest.main()
