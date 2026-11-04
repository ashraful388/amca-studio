from pathlib import Path
import tempfile
import unittest
from amca.assets import AssetStore, AssetError, build_asset_context

class StudioAssetPreviewTests(unittest.TestCase):
    def test_registered_read_integrity_and_project_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            store=AssetStore(directory)
            record=store.import_bytes(project_id="project-a",filename="source.txt",data=b"Evidence")
            self.assertEqual(store.content("project-a",record.record_id)[1],b"Evidence")
            with self.assertRaises(AssetError): store.content("project-b",record.record_id)
            with self.assertRaises(AssetError): store.content("../escape",record.record_id)
            source=Path(directory)/"project-a"/record.stored_relative_path
            source.write_bytes(b"tampered")
            with self.assertRaises(AssetError): store.content("project-a",record.record_id)
    def test_remove_excludes_context_preserves_bytes_and_readd_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            store=AssetStore(directory)
            record=store.import_bytes(project_id="project-a",filename="source.txt",data=b"Evidence")
            store.remove("project-a",record.asset_id)
            self.assertEqual(store.list("project-a"),())
            self.assertTrue((Path(directory)/"project-a"/record.stored_relative_path).exists())
            with self.assertRaises(AssetError): store.content("project-a",record.record_id)
            store=AssetStore(directory)
            self.assertEqual(store.list("project-a"),())
            restored=store.import_bytes(project_id="project-a",filename="source.txt",data=b"Evidence")
            self.assertEqual(restored.record_id,record.record_id)
            self.assertEqual(len(store.list("project-a")),1)
