"""28A regression tests: NEW-01 OOXML validation, artifact-claim binding,
NEW-02 canonical path, NEW-03 canonical attachment source reference."""
from __future__ import annotations

from amca.chat_history import ConversationStore
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.anti_fabrication import reconcile_anti_fabrication
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)


def _real_ooxml_bytes(content_type_part: str = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", f'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="{content_type_part}"/></Types>')
        z.writestr("word/document.xml", "<doc/>")
    return buf.getvalue()


class TestNew01OoxmlValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="28a-new01-"))
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.store = LocalPermissionStore(self.tmp / "perm.sqlite3")
        self.pid = "desktop-28anew01"
        # v1.1.8: a global level change must name a real chat.
        ConversationStore(self.tmp / "perm.sqlite3").create(
            title="28A OOXML validation", conversation_id="28anew01"
        )
        # v1.2.0: Full Computer is PC-wide and carries no folder, and a
        # chat-prefixed identity may not hold one at all.
        self.store.set_profile(
            project_id=self.pid, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.create_grant(project_id=self.pid, root_path=str(self.workspace),
                                capabilities=[FileCapability.CREATE, FileCapability.WRITE],
                                reason="test", ttl_seconds=3600)
        self.executor = LocalToolExecutor(self.store, state_root=self.tmp / "state")

    def _grant_id(self):
        return self.store.grants(self.pid, active_only=True)[0]["grant_id"]

    def test_plain_text_into_docx_fails(self):
        gid = self._grant_id()
        with self.assertRaises(ValueError) as ctx:
            self.executor.execute(project_id=self.pid, grant_id=gid, action="create",
                                  path="report.docx", content="plain text body")
        self.assertIn("not a valid OOXML", str(ctx.exception))
        self.assertFalse((self.workspace / "report.docx").exists())

    def test_real_ooxml_docx_succeeds(self):
        gid = self._grant_id()
        out = self.executor.execute(project_id=self.pid, grant_id=gid, action="create",
                                    path="ok.docx",
                                    content_base64=__import__("base64").b64encode(_real_ooxml_bytes()).decode("ascii"))
        self.assertTrue(out["created"])

    def test_pptx_xlsx_same_rule(self):
        gid = self._grant_id()
        for name in ("deck.pptx", "book.xlsx"):
            with self.assertRaises(ValueError):
                self.executor.execute(project_id=self.pid, grant_id=gid, action="create",
                                      path=name, content="plain")
            self.assertFalse((self.workspace / name).exists())


class TestArtifactClaimBinding(unittest.TestCase):
    def test_bare_artifact_created_blocked_without_record(self):
        text = "✅ Confirmation: Artifact created."
        reconciled, altered = reconcile_anti_fabrication(text, executed_tools=[])
        self.assertTrue(altered)
        self.assertIn("not executed", reconciled.lower())

    def test_artifact_claim_backed_by_verified_create_allowed(self):
        text = "✅ Confirmation: Artifact created."
        reconciled, altered = reconcile_anti_fabrication(
            text,
            executed_tools=[{"name": "filesystem.create", "action": "create",
                             "state": "success", "path": "note.docx"}],
        )
        self.assertFalse(altered)


class TestNew03AttachmentCanonicalReference(unittest.TestCase):
    def test_context_contains_canonical_source_instruction(self):
        from amca.assets import AssetRecord, AssetChunk, build_asset_context
        record = AssetRecord(
            asset_id="asset-test", project_id="proj-test", version=1,
            filename="27a-attach-test.txt", media_type="text/plain",
            size_bytes=38, sha256="a" * 64, stored_relative_path="t/27a.txt",
            source="desktop_chat_attachment", created_at="2026-09-13T00:00:00Z",
            modality="document", extraction_status="text_extracted",
            extraction_method="utf8-lines-v1", extraction_note=None,
            metadata=[],
            chunks=(AssetChunk(index=0, locator="lines:1-1",
                               text="27A attachment ingestion test payload.",
                               sha256="b" * 64),),
        )
        ctx = build_asset_context([record], question="What does the attached file say?")
        self.assertIn("CANONICAL SOURCE REFERENCE", ctx)
        self.assertIn("Do NOT attempt to read, open, or locate attachment files on the filesystem", ctx)
        self.assertIn("27A attachment ingestion test payload.", ctx)


class TestNew02CanonicalPathInToolResult(unittest.TestCase):
    def test_create_result_carries_canonical_absolute_path(self):
        tmp = Path(tempfile.mkdtemp(prefix="28a-new02-"))
        workspace = tmp / "workspace"
        workspace.mkdir()
        store = LocalPermissionStore(tmp / "perm.sqlite3")
        pid = "desktop-28anew02"
        ConversationStore(tmp / "perm.sqlite3").create(
            title="28A canonical path", conversation_id="28anew02"
        )
        # v1.2.0: Full Computer is PC-wide and carries no folder, and a
        # chat-prefixed identity may not hold one at all.  The grant below is
        # what scopes this proof, exactly as it did before.
        store.set_profile(project_id=pid, profile=PermissionProfile.FULL_COMPUTER)
        store.create_grant(project_id=pid, root_path=str(workspace),
                           capabilities=[FileCapability.CREATE], reason="t", ttl_seconds=3600)
        executor = LocalToolExecutor(store, state_root=tmp / "state")
        gid = store.grants(pid, active_only=True)[0]["grant_id"]
        out = executor.execute(project_id=pid, grant_id=gid, action="create",
                               path="note.txt", content="X")
        root = Path(out["root"])
        self.assertTrue((root / "note.txt").exists())
        self.assertEqual(str(root), str(workspace.resolve()))


if __name__ == "__main__":
    unittest.main()
