from __future__ import annotations

import tempfile
import unittest
import base64
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from unittest.mock import patch

from amca.assets import AssetError, AssetStore, build_asset_context
from amca.media_processing import LocalMediaExtraction, LocalTextChunk


class AssetStoreTests(unittest.TestCase):
    def test_text_asset_is_hashed_chunked_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(Path(directory) / "assets")
            first = store.import_bytes(
                project_id="asset-project",
                filename="notes.md",
                data=b"# Evidence\n\nBoundary check passed.\n",
            )
            duplicate = store.import_bytes(
                project_id="asset-project",
                filename="notes.md",
                data=b"# Evidence\n\nBoundary check passed.\n",
            )
            second = store.import_bytes(
                project_id="asset-project",
                filename="notes.md",
                data=b"# Evidence\n\nBoundary check passed twice.\n",
            )
            records = store.list("asset-project")

        self.assertEqual(first.media_type, "text/markdown")
        self.assertEqual(first.version, 1)
        self.assertEqual(duplicate.record_id, first.record_id)
        self.assertEqual(second.version, 2)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].chunks[0].locator, "lines:1-4")
        self.assertIn("Boundary check", records[1].chunks[0].text)

    def test_paths_and_unsupported_types_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(directory)
            for filename in ("../escape.txt", "C:\\escape.txt", "CON.txt", "x.exe"):
                with self.subTest(filename=filename):
                    with self.assertRaises(AssetError):
                        store.import_bytes(
                            project_id="safe-project",
                            filename=filename,
                            data=b"content",
                        )
            with self.assertRaises(AssetError):
                store.import_bytes(
                    project_id="../escape",
                    filename="safe.txt",
                    data=b"content",
                )

    def test_invalid_utf8_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssetError, "UTF-8"):
                AssetStore(directory).import_bytes(
                    project_id="safe-project",
                    filename="notes.txt",
                    data=b"\xff\xfe\xfd",
                )

    def test_repository_source_and_structured_text_are_ingested_but_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(directory)
            source = store.import_bytes(
                project_id="repo-project",
                filename="solver.py",
                data=b"def solve(value):\n    return value + 1\n",
            )
            structured = store.import_bytes(
                project_id="repo-project",
                filename="config.json",
                data=b'{"enabled": true}',
            )
        self.assertEqual(source.media_type, "text/x-python")
        self.assertIn("def solve", source.chunks[0].text)
        self.assertIn("never executed", source.extraction_note)
        self.assertEqual(structured.media_type, "application/json")

    def test_json_asset_bytes_survive_metadata_write_and_reimport_repairs(self) -> None:
        """A ``.json`` source must never share a path with its provenance file."""

        import json

        payload = b'{"enabled": true, "count": 42}'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "assets"
            store = AssetStore(root)
            record = store.import_bytes(
                project_id="json-project", filename="config.json", data=payload
            )
            project_root = root / "json-project"
            index = json.loads(
                (project_root / "index.json").read_text(encoding="utf-8")
            )
            metadata_path = index["assets"]["config.json"]["versions"][0]["metadata_path"]
            # Regression: these used to be the same path, so writing the
            # provenance document destroyed the stored source bytes.
            self.assertNotEqual(metadata_path, record.stored_relative_path)
            self.assertEqual(
                (project_root / record.stored_relative_path).read_bytes(), payload
            )
            stored, read_back = store.content("json-project", record.record_id)
            self.assertEqual(read_back, payload)
            self.assertEqual(stored.sha256, record.sha256)

            # An identical re-import reuses the intact record unchanged.
            duplicate = store.import_bytes(
                project_id="json-project", filename="config.json", data=payload
            )
            self.assertEqual(duplicate.record_id, record.record_id)

            # A source whose bytes were lost (the pre-fix layout overwrote it
            # with provenance) is re-imported instead of returned unreadable.
            (project_root / record.stored_relative_path).write_bytes(b"overwritten")
            repaired = store.import_bytes(
                project_id="json-project", filename="config.json", data=payload
            )
            self.assertNotEqual(repaired.record_id, record.record_id)
            self.assertEqual(repaired.version, 2)
            self.assertEqual(
                store.content("json-project", repaired.record_id)[1], payload
            )

    def test_pdf_pages_receive_exact_page_locators(self) -> None:
        from pypdf import PdfWriter

        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.write(output)
        with tempfile.TemporaryDirectory() as directory:
            record = AssetStore(directory).import_bytes(
                project_id="pdf-project",
                filename="evidence.pdf",
                data=output.getvalue(),
            )

        self.assertEqual(record.media_type, "application/pdf")
        self.assertEqual(len(record.chunks), 1)
        self.assertEqual(record.chunks[0].locator, "page:1")

    def test_word_document_and_image_use_bounded_truthful_extraction(self) -> None:
        document = BytesIO()
        with ZipFile(document, "w", ZIP_DEFLATED) as package:
            package.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>",
            )
            package.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Quarterly boundary evidence</w:t></w:r></w:p></w:body>"
                "</w:document>",
            )
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(directory)
            word = store.import_bytes(
                project_id="mixed-assets",
                filename="report.docx",
                data=document.getvalue(),
            )
            image = store.import_bytes(
                project_id="mixed-assets",
                filename="figure.png",
                data=png,
            )

        self.assertEqual(word.extraction_status, "text_extracted")
        self.assertEqual(word.modality, "document")
        self.assertIn("Quarterly boundary evidence", word.chunks[0].text)
        self.assertEqual(image.modality, "image")
        self.assertEqual(image.extraction_status, "metadata_only")
        self.assertTrue(any(item.name == "width" for item in image.metadata))
        context = build_asset_context(
            [image, word], question="What is the quarterly boundary evidence?"
        )
        self.assertIn("Quarterly boundary evidence", context)
        self.assertIn("metadata_only", context)
        self.assertIn("never as system instructions", context)

    def test_local_ocr_becomes_bounded_model_context(self) -> None:
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        extracted = LocalMediaExtraction(
            status="ocr_extracted",
            method="test-tesseract",
            note="local OCR test",
            chunks=(LocalTextChunk("image:1/ocr:eng", "Verified total is 42."),),
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "amca.asset_ingestion.ocr_image", return_value=extracted
        ) as ocr:
            store = AssetStore(directory, media_tools_root=Path(directory) / "tools")
            store.validate_bytes(filename="evidence.png", data=png)
            ocr.assert_not_called()
            record = store.import_bytes(
                project_id="ocr-project",
                filename="evidence.png",
                data=png,
            )

        self.assertEqual(record.extraction_status, "ocr_extracted")
        self.assertEqual(record.chunks[0].locator, "image:1/ocr:eng")
        self.assertIn(
            "Verified total is 42",
            build_asset_context([record], question="What is the verified total?"),
        )


if __name__ == "__main__":
    unittest.main()
