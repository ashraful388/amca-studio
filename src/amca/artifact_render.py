"""Deterministic local renditions for the artifact engine (W1.2).

Every export is derived from the artifact's canonical source.  Conversions
that need optional third-party libraries report ``converter_unavailable``
truthfully when the library is absent — never fabricated output.
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from amca.artifacts import ArtifactStore


def _markdown_to_html(source: str) -> str:
    """Minimal deterministic renderer: headings, fenced code, paragraphs."""

    escaped_lines: list[str] = []
    in_fence = False
    for line in source.splitlines():
        if line.startswith("```"):
            escaped_lines.append(
                "<pre><code>" if not in_fence else "</code></pre>"
            )
            in_fence = not in_fence
            continue
        if in_fence:
            escaped_lines.append(html.escape(line))
            continue
        heading = re.fullmatch(r"(#{1,6})\s+(.*)", line)
        if heading:
            level = len(heading.group(1))
            escaped_lines.append(
                f"<h{level}>{html.escape(heading.group(2))}</h{level}>"
            )
        elif line.strip():
            escaped_lines.append(f"<p>{html.escape(line)}</p>")
        else:
            escaped_lines.append("")
    body = "\n".join(escaped_lines)
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        "<title>AMCA artifact</title></head><body>\n" + body + "\n</body></html>"
    )


def _markdown_to_docx_bytes(source: str) -> bytes:
    """Heading/code-fence/list/paragraph/emphasis structure via python-docx."""

    import io
    import re

    import docx  # type: ignore[import-not-found]

    document = docx.Document()
    in_fence = False
    fence_lines: list[str] = []

    def _add_styled_runs(paragraph: Any, text: str) -> None:
        # Phase 1: tokenize inline code spans (`` `...` ``) so their
        # contents are never exposed to the emphasis regex.
        _CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
        tokens: list[tuple[str, str]] = []  # (kind, content)
        last = 0
        for m in _CODE_SPAN_RE.finditer(text):
            if m.start() > last:
                tokens.append(("md", text[last : m.start()]))
            tokens.append(("code", m.group(1)))
            last = m.end()
        if last < len(text):
            tokens.append(("md", text[last:]))

        # Phase 2: emphasis regex — underscore emphasis requires word
        # boundaries so intra-word underscores (my_var_name) stay literal.
        #   **bold**  |  __bold__  |  *italic*  |  _italic_ (word-boundary)
        _EMPHASIS_RE = re.compile(
            r"(\*\*[^\*\n]+\*\*"             # **bold**
            r"|(?<!\w)__[^_\n]+__(?!\w)"     # __bold__ (word-boundary gated)
            r"|(?<!\*)\*[^\*\n]+\*(?!\*)"     # *italic*
            r"|(?<!\w)_[^_\n]+_(?!\w))"       # _italic_ (word-boundary gated)
        )

        for kind, content in tokens:
            if kind == "code":
                from docx.shared import Pt  # type: ignore[import-not-found]
                run = paragraph.add_run(content)
                run.font.name = "Consolas"
                run.font.size = Pt(9)
                continue
            # kind == "md": apply emphasis parsing
            parts = _EMPHASIS_RE.split(content)
            for part in parts:
                if not part:
                    continue
                if (part.startswith("**") and part.endswith("**") and len(part) >= 4) or (
                    part.startswith("__") and part.endswith("__") and len(part) >= 4
                ):
                    run = paragraph.add_run(part[2:-2])
                    run.bold = True
                elif part.startswith("*") and part.endswith("*") and len(part) >= 2:
                    run = paragraph.add_run(part[1:-1])
                    run.italic = True
                elif part.startswith("_") and part.endswith("_") and len(part) >= 2:
                    run = paragraph.add_run(part[1:-1])
                    run.italic = True
                else:
                    paragraph.add_run(part)

    for line in source.splitlines():
        if line.startswith("```"):
            if in_fence:
                document.add_paragraph("\n".join(fence_lines), style="No Spacing")
                fence_lines = []
            in_fence = not in_fence
            continue
        if in_fence:
            fence_lines.append(line)
            continue
        heading = re.fullmatch(r"(#{1,6})\s+(.*)", line)
        if heading:
            document.add_heading(heading.group(2).strip(), level=len(heading.group(1)))
            continue
        bullet = re.fullmatch(r"^(\s*)[-*+]\s+(.*)", line)
        if bullet:
            p = document.add_paragraph(style="List Bullet")
            _add_styled_runs(p, bullet.group(2))
            continue
        numbered = re.fullmatch(r"^(\s*)\d+\.\s+(.*)", line)
        if numbered:
            p = document.add_paragraph(style="List Number")
            _add_styled_runs(p, numbered.group(2))
            continue
        if line.strip():
            p = document.add_paragraph()
            _add_styled_runs(p, line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _csv_to_xlsx_bytes(source: str) -> bytes:
    """Row/column transfer via openpyxl (naive comma split, quoted cells unwrapped)."""

    import csv
    import io

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    def _typed(cell: str):
        stripped = cell.strip()
        try:
            return int(stripped)
        except ValueError:
            pass
        try:
            return float(stripped)
        except ValueError:
            return cell

    for row in csv.reader(io.StringIO(source)):
        sheet.append([_typed(cell) for cell in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _json_to_pptx_bytes(source: str) -> bytes:
    """{"slides":[{"title":..., "bullets":[...]}]} -> deck via python-pptx."""

    import io
    import json

    from pptx import Presentation  # type: ignore[import-not-found]

    payload = json.loads(source)
    slides = payload.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("pptx source requires a non-empty slides list")
    presentation = Presentation()
    for index, slide_spec in enumerate(slides):
        if not isinstance(slide_spec, dict) or not isinstance(
            slide_spec.get("title"), str
        ):
            raise ValueError(f"slide {index} requires a string title")
        layout = presentation.slide_layouts[0 if index == 0 else 1]
        slide = presentation.slides.add_slide(layout)
        slide.shapes.title.text = slide_spec["title"]
        bullets = slide_spec.get("bullets", [])
        if bullets:
            body = slide.placeholders[1].text_frame
            body.text = str(bullets[0])
            for bullet in bullets[1:]:
                body.add_paragraph().text = str(bullet)
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


class ArtifactExporter:
    """Rendition builder bound to one ArtifactStore."""

    def __init__(self, store: ArtifactStore, renditions_root: Path) -> None:
        self.store = store
        self.renditions_root = Path(renditions_root)

    def export(
        self,
        *,
        project_id: str,
        artifact_id: str,
        target_format: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        meta = self.store.get(artifact_id)
        del project_id  # authorization happens at the service boundary
        artifact_type = str(meta["artifact_type"])
        source = self.store.read_source(artifact_id, version)
        version = int(source["version"])

        if artifact_type in {"bib", "json"} and target_format == artifact_type:
            content_bytes = source["content"].encode("utf-8")
        elif artifact_type == "pptx" and target_format == "pptx":
            try:
                content_bytes = _json_to_pptx_bytes(source["content"])
            except ImportError as missing:
                return {
                    "status": "converter_unavailable",
                    "reason": f"python-pptx ({getattr(missing, 'name', None) or 'pptx'!r}) is not installed; install it to enable this export",
                    "artifact_id": artifact_id,
                    "target_format": target_format,
                    "version": version,
                }
            except (ValueError, json.JSONDecodeError) as invalid:
                # A well-formed library with a malformed source: the
                # availability promise holds (the pipeline exists), but this
                # artifact's source cannot be rendered.  Structured failure,
                # never a bare ValueError to the caller.
                from amca.artifacts import ArtifactError

                raise ArtifactError(
                    f"pptx source for {artifact_id} is not renderable: {invalid}"
                ) from invalid
        elif artifact_type == "markdown" and target_format == "docx":
            try:
                content_bytes = _markdown_to_docx_bytes(source["content"])
            except ImportError as missing:
                return {
                    "status": "converter_unavailable",
                    "reason": f"python-docx ({getattr(missing, 'name', None) or 'docx'!r}) is not installed; install it to enable this export",
                    "artifact_id": artifact_id,
                    "target_format": target_format,
                    "version": version,
                }
        elif artifact_type == "xlsx" and target_format == "xlsx":
            try:
                content_bytes = _csv_to_xlsx_bytes(source["content"])
            except ImportError as missing:
                return {
                    "status": "converter_unavailable",
                    "reason": f"openpyxl ({getattr(missing, 'name', None) or 'openpyxl'!r}) is not installed; install it to enable this export",
                    "artifact_id": artifact_id,
                    "target_format": target_format,
                    "version": version,
                }
        elif artifact_type == "markdown" and target_format == "html":
            content = _markdown_to_html(source["content"])
            content_bytes = content.encode("utf-8")
        elif target_format == "zip":
            bundle_buffer = io.BytesIO()
            with zipfile.ZipFile(
                bundle_buffer, "w", zipfile.ZIP_DEFLATED
            ) as bundle:
                bundle.writestr(
                    f"source{self._canonical_ext(artifact_type)}",
                    source["content"],
                )
                provenance = json_dumps(meta)
                bundle.writestr("provenance.json", provenance)
            content_bytes = bundle_buffer.getvalue()
        else:
            available, reason = _optional_converter_available(
                artifact_type, target_format
            )
            if not available:
                return {
                    "status": "converter_unavailable",
                    "reason": reason,
                    "artifact_id": artifact_id,
                    "target_format": target_format,
                    "version": version,
                }
            # Unreachable for every pair the availability function reports
            # as available today (all have explicit export branches above),
            # but kept as a fail-closed guard: never fabricate output and
            # never raise a bare KeyError for a future pair.
            converter_name = _OPTIONAL_CONVERTERS.get(
                (artifact_type, target_format), (target_format, target_format)
            )[1]
            return {
                "status": "converter_unavailable",
                "reason": (
                    f"{target_format} export from {artifact_type} is not implemented "
                    f"in this build; installing {converter_name} "
                    "alone does not enable it"
                ),
                "artifact_id": artifact_id,
                "target_format": target_format,
                "version": version,
            }

        relative_dir = Path(artifact_id)
        out_dir = self.renditions_root / relative_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"v{version}.{target_format}"
        out_path.write_bytes(content_bytes)
        return {
            "status": "exported",
            "artifact_id": artifact_id,
            "target_format": target_format,
            "version": version,
            **({"content_type": "text/x-bibtex"} if target_format == "bib" else {}),
            "bytes": len(content_bytes),
            "path": str(out_path),
        }

    @staticmethod
    def _canonical_ext(artifact_type: str) -> str:
        from amca.artifacts import CANONICAL_SOURCE_EXT

        return CANONICAL_SOURCE_EXT.get(artifact_type, ".txt")


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


#: Targets that declare an optional third-party library.  markdown->docx,
#: csv(xlsx)->xlsx and pptx->pptx are implemented in this build; every other
#: declared target (pdf authoring, markdown->xlsx) has no rendition pipeline
#: yet and reports ``converter_unavailable`` whether or not the library
#: imports.  zip bundling and markdown->html are pure-stdlib and always
#: available; bib/json identity exports are trivially available.
_OPTIONAL_CONVERTERS: dict[tuple[str, str], tuple[str, str]] = {
    ("markdown", "pdf"): ("fitz", "PyMuPDF"),
    ("markdown", "xlsx"): ("openpyxl", "openpyxl"),
    ("markdown", "docx"): ("docx", "python-docx"),
    ("docx", "pdf"): ("fitz", "PyMuPDF"),
    ("pptx", "pptx"): ("pptx", "python-pptx"),
    ("xlsx", "xlsx"): ("openpyxl", "openpyxl"),
}

#: Renditions this build actually implements in ArtifactExporter.export,
#: mapped to the import required to enable them (None = pure-local/stdlib).
#: Availability must reflect pipeline reality, never mere importability.
PIPELINE_IMPLEMENTED: dict[tuple[str, str], str | None] = {
    ("bib", "bib"): None,
    ("json", "json"): None,
    ("markdown", "docx"): "docx",
    ("markdown", "html"): None,
    ("xlsx", "xlsx"): "openpyxl",
    ("pptx", "pptx"): "pptx",
}


def _optional_converter_available(
    artifact_type: str, target_format: str
) -> tuple[bool, str]:
    key = (artifact_type, target_format)
    # zip bundling is pure-stdlib and implemented for every source type.
    if target_format == "zip":
        return True, ""
    # Availability reflects implemented pipelines, never mere importability:
    # a pair with no export branch reports unavailable even if its library
    # happens to import (e.g. markdown->pdf with PyMuPDF present).
    if key not in PIPELINE_IMPLEMENTED:
        if key in _OPTIONAL_CONVERTERS:
            human_name = _OPTIONAL_CONVERTERS[key][1]
            return (
                False,
                f"{target_format} export from {artifact_type} is not implemented "
                f"in this build; installing {human_name} alone does not enable it",
            )
        return (
            False,
            f"no rendition defined for {artifact_type!r}->{target_format!r}",
        )
    module_name = PIPELINE_IMPLEMENTED[key]
    if module_name is None:
        return True, ""
    human_name = _OPTIONAL_CONVERTERS.get(key, (module_name, module_name))[1]
    try:
        __import__(module_name)
    except ImportError:
        return (
            False,
            f"{human_name} is not installed; install it to enable this export",
        )
    return True, ""


def render_capabilities() -> list[dict[str, Any]]:
    """Report every rendition this build can and cannot produce.

    The export path already reports ``converter_unavailable`` truthfully, but
    only after a user asks for the file.  This lets the UI say up front which
    formats are available, so a missing converter reads as a known limitation
    rather than a failed click.
    """

    entries: list[dict[str, Any]] = []
    # Advertise the union of the optional-converter table, the
    # implemented-pipeline map, and zip bundling (pure-stdlib, implemented
    # for every source type in export), so always-available renditions are
    # listed too instead of reading as "no rendition defined".
    from amca.artifacts import ARTIFACT_TYPES

    pairs = sorted(
        set(_OPTIONAL_CONVERTERS)
        | set(PIPELINE_IMPLEMENTED)
        | {(artifact_type, "zip") for artifact_type in ARTIFACT_TYPES}
    )
    for artifact_type, target_format in pairs:
        available, reason = _optional_converter_available(artifact_type, target_format)
        human_name = _OPTIONAL_CONVERTERS.get(
            (artifact_type, target_format), ("stdlib", "built in")
        )[1]
        entries.append(
            {
                "artifact_type": artifact_type,
                "target_format": target_format,
                "converter": human_name,
                "available": available,
                "reason": "" if available else reason,
            }
        )
    return entries


def rendition_available(artifact_type: str, target_format: str) -> tuple[bool, str]:
    """Whether a specific rendition can be produced, and why not when it cannot."""

    return _optional_converter_available(str(artifact_type), str(target_format))


__all__ = ["ArtifactExporter", "render_capabilities", "rendition_available"]
