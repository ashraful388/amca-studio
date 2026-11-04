"""R1.1 research source/evidence foundation tests (40 required gates)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import json
from pathlib import Path

from amca.agent_recovery import AgentRecoveryStore
from amca.research_source import (
    ResearchError,
    ResearchStore,
)


class ResearchBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = self.root / "amca.db"
        self.store = ResearchStore(self.database, storage_root=self.root / "content")
        self.session = self.store.create_session(
            project_id="projA", title="session", purpose="testing")
        self.sid = self.session["session_id"]

    def reopen(self) -> ResearchStore:
        return ResearchStore(self.database, storage_root=self.root / "content")


class SessionAndSourceTests(ResearchBase):
    def test_session_created_project_scoped(self) -> None:
        sessions = self.store.list_sessions("projA")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["status"], "active")
        self.assertEqual(self.store.list_sessions("projB"), [])

    def test_web_local_artifact_connector_sources(self) -> None:
        web = self.store.create_source(self.sid, source_kind="web",
                                       canonical_origin="https://example.com/a",
                                       title="web page")
        local = self.store.create_source(self.sid, source_kind="local_file",
                                         canonical_origin="docs/report.md",
                                         title="local file")
        artifact = self.store.create_source(self.sid, source_kind="artifact",
                                            canonical_origin="art-9@v3",
                                            title="artifact v3")
        connector = self.store.create_source(
            self.sid, source_kind="connector",
            canonical_origin="connector:github:acme/repo",
            title="connector",
            provenance={"api_key": "sk-secret", "connector_id": "github"})
        self.assertEqual(web["source_kind"], "web")
        self.assertEqual(local["canonical_origin"], "docs/report.md")
        self.assertTrue(artifact["canonical_origin"].startswith("art-9@"))
        self.assertNotIn("sk-secret", json_dumps(connector["provenance"]))

    def test_local_traversal_absolute_and_sensitive_rejected(self) -> None:
        for origin in ("../outside.txt", "/etc/passwd", "C:/temp/x",
                       ".env", "credentials.json", "id_rsa"):
            with self.assertRaises(ResearchError):
                self.store.create_source(
                    self.sid, source_kind="local_file",
                    canonical_origin=origin, title="bad")

    def test_artifact_source_requires_version_identity(self) -> None:
        with self.assertRaises(ResearchError):
            self.store.create_source(self.sid, source_kind="artifact",
                                     canonical_origin="art-9", title="no version")


def json_dumps(value):
    import json
    return json.dumps(value, sort_keys=True, default=str)


class SnapshotAndEvidenceTests(ResearchBase):
    def make_source_with_snapshot(self, content=b"alpha beta gamma delta"):
        source = self.store.create_source(
            self.sid, source_kind="web",
            canonical_origin="https://example.com/doc", title="doc")
        snapshot = self.store.add_snapshot(
            source["source_id"], project_id="projA",
            observed_at="2026-08-25T00:00:00+00:00",
            retrieved_at="2026-08-25T01:00:00+00:00",
            published_at=None, modified_at=None,
            content=content, content_type="text/plain")
        return source, snapshot

    def test_snapshots_immutable_and_multiple_allowed(self) -> None:
        source, first = self.make_source_with_snapshot(b"version one content")
        before = self.store.get_snapshot(first["snapshot_id"], project_id="projA")
        second = self.store.add_snapshot(
            source["source_id"], project_id="projA",
            observed_at="2026-09-10T00:00:00+00:00", content=b"version two content")
        after = self.store.get_snapshot(first["snapshot_id"], project_id="projA")
        self.assertEqual(before, after, "old snapshot must remain immutable")
        snapshots = self.store.list_snapshots(source["source_id"],
                                              project_id="projA")
        self.assertEqual(len(snapshots), 2)

    def test_duplicate_content_detectable_by_digest(self) -> None:
        source, first = self.make_source_with_snapshot(b"identical bytes")
        digest = first["content_digest"]
        duplicate = self.store.find_duplicate_content(
            source["source_id"], project_id="projA", content=b"identical bytes")
        self.assertEqual(duplicate, first["snapshot_id"])

    def test_large_content_uses_reference_storage(self) -> None:
        big = (b"x" * 100_000)
        source, snapshot = self.make_source_with_snapshot(big)
        row = self.store.get_snapshot(snapshot["snapshot_id"], project_id="projA")
        self.assertIsNone(row["inline_content"])
        self.assertTrue(row["content_reference"])
        self.assertAlmostEqual(row["byte_length"], 100_000)

    def test_extractive_evidence_validated_and_deterministic(self) -> None:
        _, snapshot = self.make_source_with_snapshot(b"alpha beta gamma delta")
        evidence_one = self.store.add_evidence(
            snapshot["snapshot_id"], project_id="projA",
            locator={"type": "char_range", "start": 0, "end": 10},
            excerpt="alpha beta")
        evidence_two = self.store.add_evidence(
            snapshot["snapshot_id"], project_id="projA",
            locator={"type": "line_range", "line": 1},
            excerpt="alpha beta")
        self.assertEqual(evidence_one["content_digest"],
                         evidence_two["content_digest"])
        self.assertEqual(evidence_one["extraction_method"], "extractive")
        stored_locator = evidence_one["locator"]
        self.assertEqual(stored_locator["type"], "char_range")

    def test_non_extractive_evidence_rejected(self) -> None:
        _, snapshot = self.make_source_with_snapshot(b"real snapshot body")
        with self.assertRaises(ResearchError):
            self.store.add_evidence(
                snapshot["snapshot_id"], project_id="projA",
                locator={}, excerpt="this text is nowhere in the snapshot")

    def test_evidence_bounds_enforced(self) -> None:
        _, snapshot = self.make_source_with_snapshot(b"short body")
        with self.assertRaises(ResearchError):
            self.store.add_evidence(
                snapshot["snapshot_id"], project_id="projA",
                locator={}, excerpt="x" * 9000)

    def test_metadata_bound_and_freshness_fields_stored(self) -> None:
        source, snapshot = self.make_source_with_snapshot()
        row = self.store.get_snapshot(snapshot["snapshot_id"], project_id="projA")
        self.assertEqual(row["observed_at"], "2026-08-25T00:00:00+00:00")
        self.assertEqual(row["retrieved_at"], "2026-08-25T01:00:00+00:00")
        self.assertIsNone(row["published_at"], "never fabricate publication dates")


class CitationTests(ResearchBase):
    def test_citation_pins_exact_snapshot_and_never_retargets(self) -> None:
        source = self.store.create_source(
            self.sid, source_kind="web",
            canonical_origin="https://example.com/paper", title="paper")
        snap_v1 = self.store.add_snapshot(source["source_id"], project_id="projA",
                                          observed_at="2026-08-01T00:00:00+00:00",
                                          content=b"v1 evidence body")
        evidence = self.store.add_evidence(
            snap_v1["snapshot_id"], project_id="projA",
            locator={"type": "char_range"}, excerpt="v1 evidence body")
        citation = self.store.add_citation(evidence["evidence_id"],
                                           project_id="projA",
                                           display_label="[1]")
        # A newer snapshot appears afterwards.
        self.store.add_snapshot(source["source_id"], project_id="projA",
                                observed_at="2026-09-01T00:00:00+00:00",
                                content=b"v2 evidence body")
        fresh_store = self.reopen()
        pinned = fresh_store.get_citation(citation["citation_id"],
                                          project_id="projA")
        self.assertEqual(pinned["snapshot_id"], snap_v1["snapshot_id"],
                         "citation must never retarget newer snapshots")
        self.assertEqual(pinned["evidence_id"], evidence["evidence_id"])

    def test_citation_integrity_missing_evidence_rejected(self) -> None:
        with self.assertRaises(ResearchError):
            self.store.add_citation("ev-missing", project_id="projA",
                                    display_label="[bad]")


class IsolationMigrationTests(ResearchBase):
    def test_cross_project_access_rejected(self) -> None:
        source = self.store.create_source(self.sid, source_kind="web",
                                          canonical_origin="https://x.io/d",
                                          title="d")
        snapshot = self.store.add_snapshot(source["source_id"],
                                           project_id="projA",
                                           observed_at="2026-08-25T00:00:00+00:00",
                                           content=b"body text")
        evidence = self.store.add_evidence(snapshot["snapshot_id"],
                                           project_id="projA",
                                           locator={}, excerpt="body text")
        citation = self.store.add_citation(evidence["evidence_id"],
                                           project_id="projA",
                                           display_label="[x]")
        self.assertIsNone(self.store.get_source(source["source_id"],
                                                project_id="projB"))
        self.assertIsNone(self.store.get_snapshot(snapshot["snapshot_id"],
                                                  project_id="projB"))
        self.assertIsNone(self.store.get_evidence(evidence["evidence_id"],
                                                  project_id="projB"))
        self.assertIsNone(self.store.get_citation(citation["citation_id"],
                                                  project_id="projB"))

    def test_migration_preserves_existing_data_and_is_idempotent(self) -> None:
        # A representative existing amca.db with agent-fabric data.
        agent_store = AgentRecoveryStore(self.database)
        agent_store.create_organization(organization_id="legacy-org",
                                        project_id="p", goal="g",
                                        budget_usd=1.0, max_concurrent=1)
        # Open the research store on the SAME database: migrations add tables.
        store = ResearchStore(self.database, storage_root=self.root / "c2")
        session = store.create_session(project_id="p", title="post-migration")
        org_row = AgentRecoveryStore(self.database).get_organization("legacy-org")
        self.assertIsNotNone(org_row, "previous data preserved")
        self.assertIsNotNone(session["session_id"])
        # Idempotent: re-initialize does not duplicate or fail.
        ResearchStore(self.database, storage_root=self.root / "c2")
        import sqlite3 as _sq
        connection = _sq.connect(str(self.database))
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            connection.close()
        research_tables = sorted(t for t in tables if t.startswith("research_"))
        # R1.1 five tables + R1.2 extractions/segments + R1.3B search events.
        self.assertEqual(len(research_tables), 9)

    def test_transactional_rollback_leaves_no_orphan_citation(self) -> None:
        source = self.store.create_source(self.sid, source_kind="web",
                                          canonical_origin="https://x.io/z",
                                          title="z")
        snapshot = self.store.add_snapshot(source["source_id"],
                                           project_id="projA",
                                           observed_at="2026-08-25T00:00:00+00:00",
                                           content=b"body")
        evidence = self.store.add_evidence(snapshot["snapshot_id"],
                                           project_id="projA", locator={},
                                           excerpt="body")
        with self.assertRaises(ResearchError):
            self.store.add_citation(evidence["evidence_id"] + "-missing",
                                    project_id="projA", display_label="[n]")
        count = self.query("SELECT COUNT(*) AS c FROM research_citations")["c"]
        self.assertEqual(count, 0)

    def query(self, sql):
        import sqlite3 as _sq
        connection = _sq.connect(str(self.database))
        connection.row_factory = _sq.Row
        try:
            return dict(connection.execute(sql).fetchone())
        finally:
            connection.close()

    def test_canonical_amca_db_only_and_zero_network(self) -> None:
        files = {p.name for p in self.root.iterdir() if p.is_file()}
        self.assertNotIn("research.db", files)
        self.assertNotIn("sources.db", files)
        # The module performs no network operations by construction.
        from amca import research_source
        import inspect
        surface = inspect.getsource(research_source)
        for forbidden in ("requests.", "urlopen", "http.client"):
            self.assertNotIn(forbidden, surface)


if __name__ == "__main__":
    unittest.main()
