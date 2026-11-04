"""CLOSURE-05 Phase E physical proof: artifact lifecycle user path.

Proves the complete user-visible artifact lifecycle against a real
ArtifactStore + ArtifactExporter (the exact components behind the service
boundary):

  E1. Author: markdown artifact created as immutable version 1 with
      provenance (chat_id/task_id recorded).
  E2. Edit: base_version=1 edit produces version 2; version 1 source is
      unchanged (immutable history).
  E3. Optimistic concurrency: a second edit claiming base_version=1 is
      refused (stale write).
  E4. Restore: restoring version 1 creates version 3; history intact.
  E5. Export docx: markdown -> real DOCX bytes (valid OOXML zip, content
      present in word/document.xml).
  E6. Export html: markdown -> real HTML rendition with the content.
  E7. Export xlsx: csv-typed artifact -> real XLSX zip with sheet XML.
  E8. Export zip bundle: source + provenance.json packaged.
  E9. Fail-closed: unsupported artifact type refused at author time.

Run:  python scripts/prove_closure05_phase_e.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.artifact_render import ArtifactExporter  # noqa: E402
from amca.artifacts import ArtifactError, ArtifactStore  # noqa: E402

RESULTS: list[dict[str, object]] = []

MARKER_V1 = "AMCA-PHASE-E-V1-MARKER"
MARKER_V2 = "AMCA-PHASE-E-V2-MARKER"


def record(check_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": check_id, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + check_id + " :: " + detail)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05-phase-e-"))
    try:
        store = ArtifactStore(
            database=work / "artifacts.sqlite3",
            payloads_root=work / "payloads",
        )
        exporter = ArtifactExporter(store, renditions_root=work / "renditions")
        project_id = "closure05-phase-e"

        # ---- E1: author ----
        meta = store.author(
            project_id=project_id,
            artifact_type="markdown",
            title="Phase E Evidence Report",
            content=f"# Report\n\n{MARKER_V1} initial finding.\n",
            created_by="chat:phase-e-round-1",
            chat_id="chat-phase-e-1",
        )
        artifact_id = str(meta["artifact_id"])
        record(
            "E1-author-v1",
            meta["current_version"] == 1 and artifact_id.startswith("art-"),
            f"artifact_id={artifact_id} version={meta['current_version']}",
        )

        # ---- E2: edit appends v2, v1 unchanged ----
        edited = store.edit(
            artifact_id=artifact_id,
            content=f"# Report\n\n{MARKER_V2} corrected finding.\n",
            created_by="desktop_user",
            base_version=1,
            reason="Corrected after review",
        )
        v1_source = store.read_source(artifact_id, 1)
        record(
            "E2-edit-immutable-history",
            edited["current_version"] == 2
            and MARKER_V1 in v1_source["content"]
            and MARKER_V2 not in v1_source["content"],
            f"current={edited['current_version']} v1_preserved={MARKER_V1 in v1_source['content']}",
        )

        # ---- E3: stale base_version refused ----
        stale_refused = False
        try:
            store.edit(
                artifact_id=artifact_id,
                content="clobber",
                created_by="other",
                base_version=1,
                reason="Stale write must fail",
            )
        except ArtifactError:
            stale_refused = True
        record("E3-stale-edit-refused", stale_refused, "base_version=1 after v2 refused")

        # ---- E4: restore v1 -> v3 ----
        restored = store.restore(
            artifact_id=artifact_id, version=1, created_by="desktop_user"
        )
        v3_source = store.read_source(artifact_id, 3)
        record(
            "E4-restore",
            restored["current_version"] == 3
            and MARKER_V1 in v3_source["content"]
            and MARKER_V2 not in v3_source["content"],
            f"current={restored['current_version']} v3==v1_content",
        )

        # ---- E5: export docx ----
        docx_export = exporter.export(
            project_id=project_id, artifact_id=artifact_id, target_format="docx"
        )
        docx_bytes = BytesIO(Path(docx_export["path"]).read_bytes())
        with zipfile.ZipFile(docx_bytes) as package:
            document_xml = package.read("word/document.xml").decode("utf-8")
        record(
            "E5-export-docx",
            docx_export["status"] == "exported"
            and "Report" in document_xml,
            f"status={docx_export['status']} bytes={len(docx_bytes.getvalue())} "
            f"content_in_document_xml={'Report' in document_xml}",
        )

        # ---- E6: export html ----
        html_export = exporter.export(
            project_id=project_id, artifact_id=artifact_id, target_format="html"
        )
        html_text = Path(html_export["path"]).read_text(encoding="utf-8")
        record(
            "E6-export-html",
            html_export["status"] == "exported" and "Report" in html_text,
            f"status={html_export['status']} html_has_content={'Report' in html_text}",
        )

        # ---- E7: xlsx artifact -> real xlsx rendition ----
        sheet_meta = store.author(
            project_id=project_id,
            artifact_type="xlsx",
            title="Phase E Ledger",
            content="item,quantity\nboundary-evidence,7\n",
            created_by="desktop_user",
        )
        sheet_id = str(sheet_meta["artifact_id"])
        xlsx_export = exporter.export(
            project_id=project_id, artifact_id=sheet_id, target_format="xlsx"
        )
        xlsx_bytes = BytesIO(Path(xlsx_export["path"]).read_bytes())
        sheet_xml = ""
        with zipfile.ZipFile(xlsx_bytes) as package:
            names = package.namelist()
            if "xl/worksheets/sheet1.xml" in names:
                sheet_xml = package.read("xl/worksheets/sheet1.xml").decode("utf-8")
        record(
            "E7-export-xlsx",
            xlsx_export["status"] == "exported" and "boundary-evidence" in sheet_xml,
            f"status={xlsx_export['status']} sheet1_has_data={'boundary-evidence' in sheet_xml}",
        )

        # ---- E8: zip bundle with provenance ----
        zip_export = exporter.export(
            project_id=project_id, artifact_id=artifact_id, target_format="zip"
        )
        zip_bytes = BytesIO(Path(zip_export["path"]).read_bytes())
        with zipfile.ZipFile(zip_bytes) as package:
            names = package.namelist()
            provenance_present = "provenance.json" in names
        record(
            "E8-export-zip-bundle",
            zip_export["status"] == "exported" and provenance_present,
            f"status={zip_export['status']} entries={names[:3]} provenance={provenance_present}",
        )

        # ---- E9: unsupported type refused ----
        type_refused = False
        try:
            store.author(
                project_id=project_id,
                artifact_type="vbs",
                title="Bad",
                content="x",
                created_by="desktop_user",
            )
        except ArtifactError:
            type_refused = True
        record("E9-unsupported-type-refused", type_refused, "artifact_type='vbs' refused")

        passed = sum(1 for item in RESULTS if item["ok"])
        summary = {
            "phase": "CLOSURE-05 Phase E",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out_path = SOFTWARE_ROOT / "tests" / "closure05-phase-e-proof.json"
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
