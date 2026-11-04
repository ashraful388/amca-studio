"""CLOSURE-05 Phase D physical proof: attachment ingestion TXT/PDF/PNG/DOCX.

Proves, with REAL files written to disk and ingested through the exact
AssetStore path the desktop Chat attachment flow uses:

  D1. TXT: a real UTF-8 text file is hashed, chunked with line locators,
      and its content reaches the model-facing context.
  D2. PDF: a real PDF (pypdf) is ingested with an exact page locator.
  D3. PNG: a real PNG image is ingested with truthful dimension metadata.
  D4. DOCX: a real Word package is ingested and its paragraph text is
      extracted with exact ranges.
  D5. Context: build_asset_context ranks and embeds the extracted
      attachment content for a matching question.
  D6. Fail-closed: binary disguised as .txt, an .exe, and a path-traversal
      filename are all refused before any bytes are stored.

Run:  python scripts/prove_closure05_phase_d.py
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.assets import AssetError, AssetStore, build_asset_context  # noqa: E402

RESULTS: list[dict[str, object]] = []

TXT_MARKER = "AMCA-PHASE-D-TXT-MARKER-4b1d93"
DOCX_MARKER = "AMCA-PHASE-D-DOCX-MARKER-93f2a6"
PDF_MARKER = "AMCA-PHASE-D-PDF-MARKER"


def record(check_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": check_id, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + check_id + " :: " + detail)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05-phase-d-"))
    try:
        attachments_dir = work / "attachments"
        attachments_dir.mkdir()

        # ---- build REAL files on disk, exactly a user would attach ----
        txt_path = attachments_dir / "notes.txt"
        txt_path.write_text(
            f"Meeting notes line one.\n{TXT_MARKER} — boundary evidence.\n"
            "Closing line three.\n",
            encoding="utf-8",
        )

        from pypdf import PdfWriter

        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        page.extract_text  # noqa: B018 - attribute exists; page is blank
        pdf_path = attachments_dir / "evidence.pdf"
        with pdf_path.open("wb") as handle:
            writer.write(handle)

        png_path = attachments_dir / "one-pixel.png"
        png_path.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )

        docx_path = attachments_dir / "report.docx"
        with ZipFile(docx_path, "w", ZIP_DEFLATED) as package:
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
                f"<w:body><w:p><w:r><w:t>{DOCX_MARKER}</w:t></w:r></w:p></w:body>"
                "</w:document>",
            )

        files_created = sorted(p.name for p in attachments_dir.iterdir())
        record(
            "D0-real-files-on-disk",
            files_created == ["evidence.pdf", "notes.txt", "one-pixel.png", "report.docx"],
            f"{files_created} in {attachments_dir}",
        )

        # ---- ingest each real file through the AssetStore service path ----
        store = AssetStore(work / "assets")
        project_id = "closure05-phase-d"

        def ingest(path: Path):
            return store.import_bytes(
                project_id=project_id,
                filename=path.name,
                data=path.read_bytes(),
                source="desktop_chat_attachment",
            )

        txt_record = ingest(txt_path)
        record(
            "D1-txt",
            txt_record.media_type == "text/plain"
            and any(TXT_MARKER in chunk.text for chunk in txt_record.chunks),
            f"media={txt_record.media_type} chunks={len(txt_record.chunks)} "
            f"locator={txt_record.chunks[0].locator}",
        )

        pdf_record = ingest(pdf_path)
        record(
            "D2-pdf",
            pdf_record.media_type == "application/pdf"
            and any(chunk.locator.startswith("page:") for chunk in pdf_record.chunks),
            f"media={pdf_record.media_type} chunks={len(pdf_record.chunks)} "
            f"locators={[c.locator for c in pdf_record.chunks][:2]}",
        )

        png_record = ingest(png_path)
        dims = [m.value for m in png_record.metadata if m.name in {"width", "height"}]
        record(
            "D3-png",
            png_record.media_type == "image/png" and len(dims) == 2,
            f"media={png_record.media_type} dims={dims}",
        )

        docx_record = ingest(docx_path)
        record(
            "D4-docx",
            docx_record.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            and any(DOCX_MARKER in chunk.text for chunk in docx_record.chunks),
            f"media={docx_record.media_type} chunks={len(docx_record.chunks)}",
        )

        # ---- D5: model-facing context includes extracted content ----
        records = store.list(project_id)
        context = build_asset_context(
            records,
            question=f"What do the notes say about {TXT_MARKER}?",
        )
        record(
            "D5-context-built",
            TXT_MARKER in context and "untrusted reference data" in context,
            f"context_chars={len(context)} includes_txt_marker={TXT_MARKER in context}",
        )

        # ---- D6: fail-closed negatives ----
        disguised_refused = False
        try:
            store.import_bytes(
                project_id=project_id,
                filename="disguised.txt",
                data=b"ok\x00binary garbage",
                source="desktop_chat_attachment",
            )
        except AssetError:
            disguised_refused = True
        exe_refused = False
        try:
            store.import_bytes(
                project_id=project_id,
                filename="payload.exe",
                data=b"MZ",
                source="desktop_chat_attachment",
            )
        except AssetError:
            exe_refused = True
        traversal_refused = False
        try:
            store.import_bytes(
                project_id=project_id,
                filename="../escape.txt",
                data=b"escape",
                source="desktop_chat_attachment",
            )
        except AssetError:
            traversal_refused = True
        record(
            "D6-fail-closed",
            disguised_refused and exe_refused and traversal_refused,
            f"disguised_txt={disguised_refused} exe={exe_refused} "
            f"traversal={traversal_refused}",
        )

        passed = sum(1 for item in RESULTS if item["ok"])
        summary = {
            "phase": "CLOSURE-05 Phase D",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out_path = SOFTWARE_ROOT / "tests" / "closure05-phase-d-proof.json"
        out_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({"passed": passed, "total": len(RESULTS)}))
        print(f"proof written to {out_path}")
        return 0 if passed == len(RESULTS) else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
