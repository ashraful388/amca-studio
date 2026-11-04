"""W1.1 artifact engine contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.artifacts import ArtifactError, ArtifactStore


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ArtifactStore(
            database=self.root / "artifacts.sqlite3",
            payloads_root=self.root / "payloads",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_author_creates_version_one_with_provenance(self) -> None:
        meta = self.store.author(
            project_id="p1",
            artifact_type="markdown",
            title="Report",
            content="# Report\nbody",
            created_by="model:gpt-x via chat c1",
            chat_id="c1",
        )
        aid = meta["artifact_id"]
        self.assertEqual(meta["current_version"], 1)
        self.assertEqual(meta["versions"][0]["operation"], "author")
        provenance = __import__("json").loads(meta["versions"][0]["provenance"])
        self.assertEqual(provenance["chat_id"], "c1")
        source = self.store.read_source(aid)
        self.assertEqual(source["content"], "# Report\nbody")

    def test_edit_appends_immutable_new_version(self) -> None:
        meta = self.store.author(
            project_id="p1", artifact_type="markdown",
            title="T", content="one", created_by="m",
        )
        aid = meta["artifact_id"]
        edited = self.store.edit(
            artifact_id=aid, content="one\ntwo", created_by="m", base_version=1
        )
        self.assertEqual(edited["current_version"], 2)
        old = self.store.read_source(aid, 1)
        self.assertEqual(old["content"], "one")
        with self.assertRaises(ArtifactError):
            self.store.edit(artifact_id=aid, content="clobber", created_by="m", base_version=1)

    def test_restore_creates_new_version_and_keeps_history(self) -> None:
        meta = self.store.author(
            project_id="p1", artifact_type="markdown", title="T", content="v1", created_by="m"
        )
        aid = meta["artifact_id"]
        self.store.edit(artifact_id=aid, content="v2", created_by="m", base_version=1)
        restored = self.store.restore(artifact_id=aid, version=1, created_by="user")
        self.assertEqual(restored["current_version"], 3)
        top = restored["versions"][-1]
        provenance = __import__("json").loads(top["provenance"])
        self.assertEqual(provenance["operation"], "restore")
        self.assertEqual(provenance["restored_from_version"], 1)
        self.assertEqual(self.store.read_source(aid, 3)["content"], "v1")
        self.assertEqual(self.store.read_source(aid, 2)["content"], "v2")

    def test_restore_to_current_fails_closed(self) -> None:
        meta = self.store.author(
            project_id="p1", artifact_type="markdown", title="T", content="x", created_by="m"
        )
        with self.assertRaises(ArtifactError):
            self.store.restore(artifact_id=meta["artifact_id"], version=1, created_by="u")

    def test_unsupported_type_rejected(self) -> None:
        with self.assertRaises(ArtifactError):
            self.store.author(
                project_id="p1", artifact_type="exe7z", title="T", content="x", created_by="m"
            )

    def test_missing_payload_fails_closed_not_fake(self) -> None:
        meta = self.store.author(
            project_id="p1", artifact_type="markdown", title="T", content="x", created_by="m"
        )
        payload = (
            self.root / "payloads" / meta["artifact_id"] / "v1.md"
        )
        payload.unlink()
        with self.assertRaises(ArtifactError):
            self.store.read_source(meta["artifact_id"])


if __name__ == "__main__":
    unittest.main()
