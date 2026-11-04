# W1.1 Native artifact engine contract (v1) — DESIGN RECORD

Status: approved by ChatGPT architecture review (2026-08-23, AMCA project
thread). This file binds the implementation that follows.

## Core invariants (all mandatory)

1. **One Artifact Engine** — all native outputs use one ArtifactStore/
   ArtifactEngine contract. No separate "chat DOCX generator" may own
   independent version/provenance semantics.
2. **Canonical source vs rendition** — every version has exactly ONE
   canonical source representation; previews/exports are DERIVED RENDITIONS
   and never become canonical.
3. **Immutable versions** — append-only snapshots; edits/regeneration/
   restore/conversion changes create a NEW version; never mutate a version.
4. **Restore does not rewind history** — restoring v1 from v3 creates v4
   with `operation=restore`, `restored_from_version=v1`; v2/v3 intact.
5. **Provenance mandatory** — no artifact without Project + creator
   provenance.
6. **Local-first** — authoring/validation/preview/export run locally; no
   silent cloud conversion.
7. **No fabricated capability** — missing optional converter ⇒ explicit
   `converter_unavailable`, never fake output or zero-byte placeholders.
8. **Structural validation ≠ semantic understanding** — metadata extraction
   alone must never create a "semantically understood" claim.

## Type registry v1

markdown, docx, pdf, tex, xlsx, pptx, image, html, zip.
Local technologies per existing execution plan: python-docx, openpyxl,
python-pptx, PyMuPDF, Markdown/LaTeX/HTML parsing. Converters are optional
explicit installs reported by the UI (whisper.cpp pattern).

## Canonical source formats (v1)

- markdown → canonical .md; renditions: .html (deterministic local).
- xlsx/csv → canonical .csv or structured JSON; rendition: .xlsx via
  openpyxl when installed.
- docx/pptx/pdf → author via python-docx/openpyxl/python-pptx/PyMuPDF from
  structured JSON source; binary output is a rendition of that JSON.
- image/zip/tex → passthrough/generation per converter availability.

## Storage layout

SQLite table `artifacts` (metadata + versions + provenance, mirroring
assets.py patterns) + immutable payloads under
`<data>/artifacts/<artifact_id>/v<N>.<ext>` with SHA-256 per version.

## API surface (mirrors workspace style)

- POST `/api/projects/{id}/artifacts` (author: type, title, source payload)
- GET  `/api/projects/{id}/artifacts` (list w/ versions+provenance)
- GET  `/api/projects/{id}/artifacts/{aid}` (metadata)
- GET  `/api/projects/{id}/artifacts/{aid}/versions/{n}` (content)
- POST `.../artifacts/{aid}/edit` (new immutable version + diff/provenance)
- POST `.../artifacts/{aid}/restore` {version}
- POST `.../artifacts/{aid}/export` {format} → rendition or
  `converter_unavailable`

## W1 stop-gate mapping

A single chat produces ≥1 document + spreadsheet + presentation + ZIP;
each appears as an artifact card, previewable/versioned/exportable with
provenance. Export requires its converter present; otherwise the response
states `converter_unavailable` truthfully.
