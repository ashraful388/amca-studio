"""Bounded, local-only extraction for AMCA project assets.

The module never executes document macros, browser content, or bundled media.
OOXML packages are inspected through a constrained ZIP reader; media parsers
read only bounded container/header metadata.  Optional ``ffprobe`` use is
limited to one local file and a fixed metadata-only command.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile, ZipInfo

from .media_processing import ocr_image, transcribe_audio


MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 4 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_ZIP_MEMBERS = 4_096
MAX_ZIP_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200
MAX_DERIVED_CHUNKS = 20_000
MAX_IMAGE_PIXELS = 100_000_000
MAX_FFPROBE_OUTPUT_BYTES = 256 * 1024
FFPROBE_TIMEOUT_SECONDS = 8
CHUNK_CHARACTERS = 4_000
CHUNK_OVERLAP = 240

_CELL_REFERENCE = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}\Z")
_XML_FORBIDDEN = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class IngestionError(ValueError):
    """Raised when bytes are malformed, unsafe, or use an unknown format."""


@dataclass(frozen=True, slots=True)
class ExtractedChunk:
    locator: str
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ExtractedMetadata:
    name: str
    value: str
    locator: str


@dataclass(frozen=True, slots=True)
class IngestionResult:
    media_type: str
    modality: str
    status: str
    method: str
    note: str
    chunks: tuple[ExtractedChunk, ...]
    metadata: tuple[ExtractedMetadata, ...]


class _ChunkBuilder:
    def __init__(self) -> None:
        self._chunks: list[ExtractedChunk] = []
        self._characters = 0

    def add(self, locator: str, text: str, *, include_empty: bool = False) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized and not include_empty:
            return
        if len(self._chunks) >= MAX_DERIVED_CHUNKS:
            raise IngestionError(
                f"derived content exceeds the {MAX_DERIVED_CHUNKS}-chunk limit"
            )
        self._characters += len(normalized)
        if self._characters > MAX_EXTRACTED_CHARACTERS:
            raise IngestionError(
                f"extracted text exceeds the {MAX_EXTRACTED_CHARACTERS}-character limit"
            )
        self._chunks.append(
            ExtractedChunk(
                locator=locator,
                text=normalized,
                sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            )
        )

    def extend_text(self, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(normalized) > MAX_EXTRACTED_CHARACTERS:
            raise IngestionError(
                f"extracted text exceeds the {MAX_EXTRACTED_CHARACTERS}-character limit"
            )
        if not normalized:
            return
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + CHUNK_CHARACTERS)
            if end < len(normalized):
                newline = normalized.rfind("\n", start + CHUNK_CHARACTERS // 2, end)
                if newline > start:
                    end = newline + 1
            content = normalized[start:end]
            line_start = normalized.count("\n", 0, start) + 1
            line_end = line_start + content.count("\n")
            self.add(f"lines:{line_start}-{line_end}", content)
            if end >= len(normalized):
                break
            start = max(start + 1, end - CHUNK_OVERLAP)

    def finish(self) -> tuple[ExtractedChunk, ...]:
        return tuple(self._chunks)


def _meta(name: str, value: object, locator: str = "bytes:0-") -> ExtractedMetadata:
    if isinstance(value, float):
        rendered = format(value, ".9g")
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = str(value)
    return ExtractedMetadata(name=name, value=rendered, locator=locator)


def _local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _attribute_by_local_name(element: ElementTree.Element, name: str) -> str | None:
    """Return an attribute value by local name.

    Namespaced keys (``{ns}id``) win over the bare key for the same local
    name. OOXML relationship references (`r:id`) coexist with plain `id`
    attributes (e.g. `p:sldId` carries both), and the relationship id is the
    semantically correct choice for every current caller.
    """
    bare_value = None
    for key, value in element.attrib.items():
        if key.rsplit("}", 1)[-1] == name:
            if "}" in key:
                return value
            bare_value = value
    return bare_value


def _parse_xml(data: bytes, label: str) -> ElementTree.Element:
    if not data:
        raise IngestionError(f"{label} is empty")
    if len(data) > MAX_ZIP_MEMBER_BYTES:
        raise IngestionError(f"{label} exceeds the XML member limit")
    if _XML_FORBIDDEN.search(data):
        raise IngestionError(f"{label} contains a forbidden DTD/entity declaration")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise IngestionError(f"{label} contains malformed XML") from error


def _safe_member_name(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise IngestionError("ZIP package contains an unsafe member name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise IngestionError("ZIP package contains a path traversal member")
    if path.parts and ":" in path.parts[0]:
        raise IngestionError("ZIP package contains a drive-qualified member")
    return path.as_posix()


class _SafeZipPackage:
    """Validate the entire central directory before reading any OOXML XML."""

    def __init__(self, data: bytes, label: str) -> None:
        if not data.startswith(b"PK"):
            raise IngestionError(f"{label} does not have a ZIP package header")
        try:
            self._zip = ZipFile(io.BytesIO(data), "r")
        except (BadZipFile, OSError) as error:
            raise IngestionError(f"{label} is not a valid ZIP package") from error
        self.label = label
        self._members: dict[str, ZipInfo] = {}
        try:
            self._validate()
        except Exception:
            self._zip.close()
            raise

    def _validate(self) -> None:
        infos = self._zip.infolist()
        if not infos or len(infos) > MAX_ZIP_MEMBERS:
            raise IngestionError(
                f"{self.label} must contain 1-{MAX_ZIP_MEMBERS} ZIP members"
            )
        total = 0
        folded_names: set[str] = set()
        for info in infos:
            name = _safe_member_name(info.filename.rstrip("/"))
            folded = name.casefold()
            if folded in folded_names:
                raise IngestionError(f"{self.label} contains duplicate ZIP member names")
            folded_names.add(folded)
            if info.flag_bits & 0x1:
                raise IngestionError(f"{self.label} contains an encrypted ZIP member")
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise IngestionError(f"{self.label} contains a symbolic-link member")
            if info.is_dir():
                continue
            if info.file_size < 0 or info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise IngestionError(
                    f"{self.label} contains a member exceeding {MAX_ZIP_MEMBER_BYTES} bytes"
                )
            total += info.file_size
            if total > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise IngestionError(
                    f"{self.label} exceeds the {MAX_ZIP_UNCOMPRESSED_BYTES}-byte expanded limit"
                )
            compressed = info.compress_size
            if info.file_size and (
                compressed <= 0
                or info.file_size > compressed * MAX_ZIP_COMPRESSION_RATIO
            ):
                raise IngestionError(f"{self.label} contains a high-ratio ZIP member")
            self._members[name] = info

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._members)

    def read(
        self,
        name: str,
        *,
        required: bool = True,
        limit: int = MAX_ZIP_MEMBER_BYTES,
    ) -> bytes | None:
        safe_name = _safe_member_name(name)
        info = self._members.get(safe_name)
        if info is None:
            if required:
                raise IngestionError(f"{self.label} is missing required member {safe_name}")
            return None
        if info.file_size > limit:
            raise IngestionError(f"{safe_name} exceeds its {limit}-byte extraction limit")
        try:
            with self._zip.open(info, "r") as handle:
                data = handle.read(limit + 1)
        except (BadZipFile, OSError, RuntimeError) as error:
            raise IngestionError(f"{safe_name} failed ZIP CRC/decompression validation") from error
        if len(data) > limit or len(data) != info.file_size:
            raise IngestionError(f"{safe_name} expanded beyond its declared bounds")
        return data

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "_SafeZipPackage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _safe_relationship_target(base: str, target: str) -> str:
    if not target or "\x00" in target or "\\" in target or ":" in target:
        raise IngestionError("OOXML relationship target is unsafe")
    if target.startswith("//"):
        # UNC-style network target must never degrade into an in-package member.
        raise IngestionError("OOXML relationship target escapes the package")
    if target.startswith("/"):
        # OPC allows package-absolute targets (openpyxl writes them); resolve
        # from the package root rather than the source part's folder.
        joined = posixpath.normpath(target.lstrip("/"))
    else:
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), target))
    if joined in (".", "..") or joined.startswith("../") or joined.startswith("/"):
        raise IngestionError("OOXML relationship target escapes the package")
    return _safe_member_name(joined)


def _relationships(package: _SafeZipPackage, path: str) -> dict[str, str]:
    raw = package.read(path, required=False)
    if raw is None:
        return {}
    root = _parse_xml(raw, path)
    result: dict[str, str] = {}
    if path == "_rels/.rels":
        base_part = "."  # package root
    else:
        base_part = path.replace("/_rels/", "/").removesuffix(".rels")
    for relation in root.iter():
        if _local_name(relation) != "Relationship":
            continue
        relation_id = relation.attrib.get("Id")
        target = relation.attrib.get("Target")
        if not relation_id or not target:
            raise IngestionError(f"{path} contains an incomplete relationship")
        if relation.attrib.get("TargetMode", "").casefold() == "external":
            continue
        if relation_id in result:
            raise IngestionError(f"{path} contains a duplicate relationship ID")
        result[relation_id] = _safe_relationship_target(base_part, target)
    return result


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        name = _local_name(node)
        if name == "t" and node.text:
            pieces.append(node.text)
        elif name == "tab":
            pieces.append("\t")
        elif name in {"br", "cr"}:
            pieces.append("\n")
    return "".join(pieces).strip()


def _docx(data: bytes) -> IngestionResult:
    builder = _ChunkBuilder()
    metadata: list[ExtractedMetadata] = []
    with _SafeZipPackage(data, "DOCX") as package:
        content_types = package.read("[Content_Types].xml")
        assert content_types is not None
        if b"wordprocessingml.document.main+xml" not in content_types:
            raise IngestionError("DOCX content types do not declare a Word document")
        main = package.read("word/document.xml")
        assert main is not None
        parts = [("document", "word/document.xml", main)]
        auxiliary = sorted(
            name
            for name in package.names
            if re.fullmatch(
                r"word/(?:header[0-9]+|footer[0-9]+|footnotes|endnotes|comments)\.xml",
                name,
            )
        )
        for name in auxiliary:
            raw = package.read(name)
            assert raw is not None
            parts.append((Path(name).stem, name, raw))

        paragraph_count = 0
        table_count = 0
        for part_label, path, raw in parts:
            root = _parse_xml(raw, path)
            table_paragraphs: set[int] = set()
            for table_number, table in enumerate(
                (node for node in root.iter() if _local_name(node) == "tbl"), start=1
            ):
                table_count += 1
                for row_number, row in enumerate(
                    (node for node in table if _local_name(node) == "tr"), start=1
                ):
                    cells = [node for node in row if _local_name(node) == "tc"]
                    for cell_number, cell in enumerate(cells, start=1):
                        paragraphs = [
                            node for node in cell.iter() if _local_name(node) == "p"
                        ]
                        table_paragraphs.update(id(node) for node in paragraphs)
                        text = "\n".join(
                            value
                            for value in (_paragraph_text(node) for node in paragraphs)
                            if value
                        )
                        builder.add(
                            f"{part_label}/table:{table_number}/row:{row_number}/cell:{cell_number}",
                            text,
                        )
            part_paragraph = 0
            for paragraph in (node for node in root.iter() if _local_name(node) == "p"):
                if id(paragraph) in table_paragraphs:
                    continue
                text = _paragraph_text(paragraph)
                if not text:
                    continue
                part_paragraph += 1
                paragraph_count += 1
                builder.add(f"{part_label}/paragraph:{part_paragraph}", text)
        metadata.extend(
            (
                _meta("paragraph_count", paragraph_count, "document"),
                _meta("table_count", table_count, "document"),
                _meta("auxiliary_text_parts", len(auxiliary), "document"),
            )
        )
    return IngestionResult(
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        modality="document",
        status="text_extracted" if builder.finish() else "metadata_only",
        method="bounded-ooxml-word-v1",
        note=(
            "Paragraphs, table cells, headers/footers, notes, and comments are local XML extraction; "
            "layout, drawings, macros, and embedded objects are not interpreted."
        ),
        chunks=builder.finish(),
        metadata=tuple(metadata),
    )


def _xlsx(data: bytes) -> IngestionResult:
    builder = _ChunkBuilder()
    metadata: list[ExtractedMetadata] = []
    with _SafeZipPackage(data, "XLSX") as package:
        content_types = package.read("[Content_Types].xml")
        assert content_types is not None
        if b"spreadsheetml.sheet.main+xml" not in content_types:
            raise IngestionError("XLSX content types do not declare an Excel workbook")
        workbook_raw = package.read("xl/workbook.xml")
        assert workbook_raw is not None
        workbook = _parse_xml(workbook_raw, "xl/workbook.xml")
        relations = _relationships(package, "xl/_rels/workbook.xml.rels")

        shared: list[str] = []
        shared_raw = package.read("xl/sharedStrings.xml", required=False)
        if shared_raw is not None:
            shared_root = _parse_xml(shared_raw, "xl/sharedStrings.xml")
            for item in (node for node in shared_root.iter() if _local_name(node) == "si"):
                shared.append(
                    "".join(
                        node.text or ""
                        for node in item.iter()
                        if _local_name(node) == "t"
                    )
                )
                if len(shared) > MAX_DERIVED_CHUNKS:
                    raise IngestionError("XLSX shared-string table exceeds the entry limit")

        sheet_count = 0
        formula_count = 0
        for sheet in (node for node in workbook.iter() if _local_name(node) == "sheet"):
            sheet_count += 1
            if sheet_count > 1_024:
                raise IngestionError("XLSX exceeds the 1024-sheet limit")
            name = sheet.attrib.get("name", "").strip()
            relation_id = _attribute_by_local_name(sheet, "id")
            if not name or len(name) > 31 or relation_id not in relations:
                raise IngestionError("XLSX contains an invalid sheet declaration")
            path = relations[relation_id]
            if not path.startswith("xl/worksheets/"):
                raise IngestionError("XLSX sheet relationship targets a non-worksheet part")
            sheet_raw = package.read(path)
            assert sheet_raw is not None
            sheet_root = _parse_xml(sheet_raw, path)
            seen_cells: set[str] = set()
            for cell in (node for node in sheet_root.iter() if _local_name(node) == "c"):
                reference = cell.attrib.get("r", "")
                if not _CELL_REFERENCE.fullmatch(reference) or reference in seen_cells:
                    raise IngestionError("XLSX contains an invalid or duplicate cell reference")
                seen_cells.add(reference)
                kind = cell.attrib.get("t", "n")
                formula_node = next(
                    (node for node in cell if _local_name(node) == "f"), None
                )
                value_node = next(
                    (node for node in cell if _local_name(node) == "v"), None
                )
                inline_node = next(
                    (node for node in cell if _local_name(node) == "is"), None
                )
                raw_value = value_node.text if value_node is not None and value_node.text else ""
                if kind == "s":
                    try:
                        value = shared[int(raw_value)]
                    except (ValueError, IndexError) as error:
                        raise IngestionError("XLSX cell has an invalid shared-string index") from error
                elif kind == "inlineStr" and inline_node is not None:
                    value = "".join(
                        node.text or ""
                        for node in inline_node.iter()
                        if _local_name(node) == "t"
                    )
                elif kind == "b":
                    value = "TRUE" if raw_value == "1" else "FALSE"
                else:
                    value = raw_value
                if formula_node is not None:
                    formula_count += 1
                    formula = formula_node.text or ""
                    rendered = f"={formula}"
                    if value:
                        rendered += f" -> {value}"
                else:
                    rendered = value
                builder.add(f"sheet:{name}!{reference}", rendered)
            metadata.append(_meta(f"sheet.{sheet_count}.name", name, f"sheet:{name}"))
            metadata.append(
                _meta(f"sheet.{sheet_count}.nonempty_cells", len(seen_cells), f"sheet:{name}")
            )
        metadata.extend(
            (
                _meta("sheet_count", sheet_count, "workbook"),
                _meta("formula_count", formula_count, "workbook"),
            )
        )
    return IngestionResult(
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        modality="spreadsheet",
        status="text_extracted" if builder.finish() else "metadata_only",
        method="bounded-ooxml-spreadsheet-v1",
        note=(
            "Stored cell values and formulas are extracted with exact sheet/cell locators; "
            "formatting, charts, macros, external links, and formula recalculation are not interpreted."
        ),
        chunks=builder.finish(),
        metadata=tuple(metadata),
    )


def _pptx(data: bytes) -> IngestionResult:
    builder = _ChunkBuilder()
    metadata: list[ExtractedMetadata] = []
    with _SafeZipPackage(data, "PPTX") as package:
        content_types = package.read("[Content_Types].xml")
        assert content_types is not None
        if b"presentationml.presentation.main+xml" not in content_types:
            raise IngestionError("PPTX content types do not declare a presentation")
        presentation_raw = package.read("ppt/presentation.xml")
        assert presentation_raw is not None
        presentation = _parse_xml(presentation_raw, "ppt/presentation.xml")
        relations = _relationships(package, "ppt/_rels/presentation.xml.rels")
        slide_number = 0
        for slide_id in (node for node in presentation.iter() if _local_name(node) == "sldId"):
            relation_id = _attribute_by_local_name(slide_id, "id")
            if relation_id not in relations:
                raise IngestionError("PPTX contains an invalid slide relationship")
            path = relations[relation_id]
            if not path.startswith("ppt/slides/"):
                raise IngestionError("PPTX slide relationship targets a non-slide part")
            slide_number += 1
            if slide_number > 2_000:
                raise IngestionError("PPTX exceeds the 2000-slide limit")
            slide_raw = package.read(path)
            assert slide_raw is not None
            slide = _parse_xml(slide_raw, path)
            paragraphs: list[str] = []
            for paragraph in (node for node in slide.iter() if _local_name(node) == "p"):
                text = "".join(
                    node.text or ""
                    for node in paragraph.iter()
                    if _local_name(node) == "t"
                ).strip()
                if text:
                    paragraphs.append(text)
            builder.add(f"slide:{slide_number}", "\n".join(paragraphs))

            rel_path = posixpath.join(
                posixpath.dirname(path),
                "_rels",
                posixpath.basename(path) + ".rels",
            )
            slide_relations = _relationships(package, rel_path)
            note_targets = [
                target
                for target in slide_relations.values()
                if target.startswith("ppt/notesSlides/")
            ]
            for target in note_targets[:1]:
                notes_raw = package.read(target)
                assert notes_raw is not None
                notes = _parse_xml(notes_raw, target)
                note_text = "\n".join(
                    text
                    for text in (
                        "".join(
                            node.text or ""
                            for node in paragraph.iter()
                            if _local_name(node) == "t"
                        ).strip()
                        for paragraph in (
                            node for node in notes.iter() if _local_name(node) == "p"
                        )
                    )
                    if text
                )
                builder.add(f"slide:{slide_number}/notes", note_text)
        metadata.append(_meta("slide_count", slide_number, "presentation"))
    return IngestionResult(
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        modality="presentation",
        status="text_extracted" if builder.finish() else "metadata_only",
        method="bounded-ooxml-presentation-v1",
        note=(
            "Slide and speaker-note text are extracted locally; layout, images, charts, "
            "animations, macros, and embedded objects are not interpreted."
        ),
        chunks=builder.finish(),
        metadata=tuple(metadata),
    )


class _BoundedHTMLText(HTMLParser):
    _IGNORED = {"script", "style", "noscript", "template", "svg", "canvas"}
    _BLOCKS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._current: list[str] = []
        self.blocks: list[str] = []
        self.title: list[str] = []
        self._in_title = False
        self._characters = 0

    def _flush(self) -> None:
        text = " ".join("".join(self._current).split())
        self._current.clear()
        if text:
            self.blocks.append(text)
            if len(self.blocks) > MAX_DERIVED_CHUNKS:
                raise IngestionError("HTML exceeds the derived block limit")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in self._IGNORED:
            self._ignored_depth += 1
        elif not self._ignored_depth and lowered in self._BLOCKS:
            self._flush()
        if lowered == "title" and not self._ignored_depth:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and lowered in self._BLOCKS:
            self._flush()
        if lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self._characters += len(data)
        if self._characters > MAX_EXTRACTED_CHARACTERS:
            raise IngestionError("HTML text exceeds the extracted-character limit")
        self._current.append(data)
        if self._in_title:
            self.title.append(data)

    def finish(self) -> None:
        self._flush()


def _html(data: bytes) -> IngestionResult:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise IngestionError("HTML assets must be valid UTF-8") from error
    parser = _BoundedHTMLText()
    try:
        parser.feed(text)
        parser.close()
        parser.finish()
    except (ValueError, AssertionError) as error:
        raise IngestionError("HTML could not be parsed safely") from error
    builder = _ChunkBuilder()
    for number, block in enumerate(parser.blocks, start=1):
        builder.add(f"html:block:{number}", block)
    title = " ".join("".join(parser.title).split())
    metadata = (_meta("title", title, "html:title"),) if title else ()
    return IngestionResult(
        media_type="text/html",
        modality="document",
        status="text_extracted" if builder.finish() else "metadata_only",
        method="stdlib-html-visible-text-v1",
        note=(
            "Visible static text is extracted as UTF-8; scripts, styles, templates, SVG, "
            "canvas content, DOM execution, linked resources, and rendered layout are excluded."
        ),
        chunks=builder.finish(),
        metadata=metadata,
    )


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width > 100_000 or height > 100_000:
        raise IngestionError("image declares invalid dimensions")
    if width * height > MAX_IMAGE_PIXELS:
        raise IngestionError(f"image exceeds the {MAX_IMAGE_PIXELS}-pixel safety limit")


def _png(data: bytes) -> tuple[str, list[ExtractedMetadata]]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise IngestionError("PNG content does not have a valid signature")
    position = 8
    chunks = 0
    width = height = bit_depth = color_type = 0
    saw_end = False
    while position < len(data):
        if position + 12 > len(data):
            raise IngestionError("PNG contains a truncated chunk")
        length = struct.unpack(">I", data[position : position + 4])[0]
        chunk_type = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data):
            raise IngestionError("PNG chunk exceeds the source bounds")
        payload = data[position + 8 : position + 8 + length]
        expected_crc = struct.unpack(">I", data[position + 8 + length : end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise IngestionError("PNG chunk CRC validation failed")
        chunks += 1
        if chunks > 100_000:
            raise IngestionError("PNG contains too many chunks")
        if chunks == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise IngestionError("PNG first chunk is not a valid IHDR")
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
            _validate_dimensions(width, height)
        if chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise IngestionError("PNG has an invalid IEND or trailing bytes")
            saw_end = True
            break
        position = end
    if not saw_end:
        raise IngestionError("PNG is missing IEND")
    return "image/png", [
        _meta("width", width, "image:1"),
        _meta("height", height, "image:1"),
        _meta("bit_depth", bit_depth, "image:1"),
        _meta("color_type", color_type, "image:1"),
    ]


def _jpeg(data: bytes) -> tuple[str, list[ExtractedMetadata]]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        raise IngestionError("JPEG content does not have a valid SOI marker")
    position = 2
    width = height = components = 0
    while position < len(data):
        if data[position] != 0xFF:
            raise IngestionError("JPEG marker stream is malformed")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker == 0xD9:
            break
        if marker == 0xDA:
            if data.rfind(b"\xff\xd9", position) < position:
                raise IngestionError("JPEG entropy stream is missing EOI")
            break
        if marker in range(0xD0, 0xD8) or marker == 0x01:
            continue
        if position + 2 > len(data):
            raise IngestionError("JPEG contains a truncated marker length")
        length = struct.unpack(">H", data[position : position + 2])[0]
        if length < 2 or position + length > len(data):
            raise IngestionError("JPEG marker exceeds the source bounds")
        if marker in {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }:
            if length < 8:
                raise IngestionError("JPEG SOF marker is too short")
            height, width = struct.unpack(">HH", data[position + 3 : position + 7])
            components = data[position + 7]
            _validate_dimensions(width, height)
        position += length
    if not width or not height:
        raise IngestionError("JPEG has no supported start-of-frame dimensions")
    return "image/jpeg", [
        _meta("width", width, "image:1"),
        _meta("height", height, "image:1"),
        _meta("components", components, "image:1"),
    ]


def _gif(data: bytes) -> tuple[str, list[ExtractedMetadata]]:
    if len(data) < 14 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        raise IngestionError("GIF content does not have a valid header")
    if data[-1:] != b";":
        raise IngestionError("GIF is missing its trailer")
    width, height = struct.unpack("<HH", data[6:10])
    _validate_dimensions(width, height)
    return "image/gif", [
        _meta("width", width, "image:1"),
        _meta("height", height, "image:1"),
        _meta("version", data[:6].decode("ascii"), "image:1"),
    ]


def _bmp(data: bytes) -> tuple[str, list[ExtractedMetadata]]:
    if len(data) < 26 or not data.startswith(b"BM"):
        raise IngestionError("BMP content does not have a valid header")
    declared = struct.unpack("<I", data[2:6])[0]
    dib_size = struct.unpack("<I", data[14:18])[0]
    if declared and declared > len(data):
        raise IngestionError("BMP declares a size beyond the source bytes")
    if dib_size == 12:
        width, height = struct.unpack("<HH", data[18:22])
    elif dib_size >= 40 and len(data) >= 26:
        width, raw_height = struct.unpack("<ii", data[18:26])
        height = abs(raw_height)
    else:
        raise IngestionError("BMP uses an unsupported DIB header")
    _validate_dimensions(width, height)
    return "image/bmp", [
        _meta("width", width, "image:1"),
        _meta("height", height, "image:1"),
        _meta("dib_header_bytes", dib_size, "image:1"),
    ]


def _webp(data: bytes) -> tuple[str, list[ExtractedMetadata]]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise IngestionError("WebP content does not have a valid RIFF/WEBP header")
    if struct.unpack("<I", data[4:8])[0] + 8 != len(data):
        raise IngestionError("WebP RIFF size does not match the source bytes")
    kind = data[12:16]
    payload_size = struct.unpack("<I", data[16:20])[0]
    payload = data[20 : 20 + payload_size]
    if len(payload) != payload_size:
        raise IngestionError("WebP first image chunk is truncated")
    if kind == b"VP8X" and len(payload) >= 10:
        width = 1 + int.from_bytes(payload[4:7], "little")
        height = 1 + int.from_bytes(payload[7:10], "little")
    elif kind == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
        bits = int.from_bytes(payload[1:5], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    elif kind == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
        width = struct.unpack("<H", payload[6:8])[0] & 0x3FFF
        height = struct.unpack("<H", payload[8:10])[0] & 0x3FFF
    else:
        raise IngestionError("WebP uses an unsupported or malformed image chunk")
    _validate_dimensions(width, height)
    return "image/webp", [
        _meta("width", width, "image:1"),
        _meta("height", height, "image:1"),
        _meta("encoding", kind.decode("ascii").strip(), "image:1"),
    ]


def _tiff(data: bytes) -> tuple[str, list[ExtractedMetadata]]:
    if len(data) < 8 or data[:4] not in {b"II*\x00", b"MM\x00*"}:
        raise IngestionError("TIFF content does not have a valid byte-order/magic header")
    order = "<" if data[:2] == b"II" else ">"
    ifd_offset = struct.unpack(order + "I", data[4:8])[0]
    if ifd_offset < 8 or ifd_offset + 2 > len(data):
        raise IngestionError("TIFF first IFD offset is outside the source bytes")
    count = struct.unpack(order + "H", data[ifd_offset : ifd_offset + 2])[0]
    if count > 4_096 or ifd_offset + 2 + count * 12 + 4 > len(data):
        raise IngestionError("TIFF first IFD exceeds bounded source data")
    dimensions: dict[int, int] = {}
    type_sizes = {3: 2, 4: 4}
    for number in range(count):
        start = ifd_offset + 2 + number * 12
        tag, field_type, values = struct.unpack(order + "HHI", data[start : start + 8])
        if tag not in {256, 257} or field_type not in type_sizes or values != 1:
            continue
        size = type_sizes[field_type]
        raw = data[start + 8 : start + 12]
        value = struct.unpack(order + ("H" if size == 2 else "I"), raw[:size])[0]
        dimensions[tag] = value
    if 256 not in dimensions or 257 not in dimensions:
        raise IngestionError("TIFF first IFD does not declare width and height")
    width, height = dimensions[256], dimensions[257]
    _validate_dimensions(width, height)
    return "image/tiff", [
        _meta("width", width, "image:1"),
        _meta("height", height, "image:1"),
        _meta("byte_order", "little" if order == "<" else "big", "image:1"),
    ]


def _image(
    suffix: str,
    data: bytes,
    *,
    media_tools_root: str | os.PathLike[str] | None = None,
    enrich: bool = True,
) -> IngestionResult:
    parser = {
        ".png": _png,
        ".jpg": _jpeg,
        ".jpeg": _jpeg,
        ".gif": _gif,
        ".bmp": _bmp,
        ".webp": _webp,
        ".tif": _tiff,
        ".tiff": _tiff,
    }[suffix]
    media_type, metadata = parser(data)
    extraction = (
        ocr_image(data, suffix=suffix, tools_root=media_tools_root)
        if enrich
        else None
    )
    builder = _ChunkBuilder()
    if extraction is not None:
        for chunk in extraction.chunks:
            builder.add(chunk.locator, chunk.text)
    return IngestionResult(
        media_type=media_type,
        modality="image",
        status=extraction.status if extraction is not None else "metadata_only",
        method=extraction.method if extraction is not None else "bounded-raster-header-v1",
        note=(
            extraction.note
            if extraction is not None
            else "Container/header metadata and source bytes are verified; semantic enrichment was not run during validation."
        ),
        chunks=builder.finish(),
        metadata=tuple(metadata),
    )


def _riff_chunks(data: bytes, expected: bytes) -> Iterator[tuple[bytes, bytes]]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != expected:
        raise IngestionError(f"RIFF content is not a valid {expected.decode('ascii')} container")
    declared = struct.unpack("<I", data[4:8])[0] + 8
    if declared > len(data) or len(data) - declared > 1:
        raise IngestionError("RIFF declared size does not match the source bytes")
    position = 12
    chunks = 0
    while position + 8 <= declared:
        kind = data[position : position + 4]
        size = struct.unpack("<I", data[position + 4 : position + 8])[0]
        end = position + 8 + size
        if end > declared:
            raise IngestionError("RIFF chunk exceeds the declared container")
        yield kind, data[position + 8 : end]
        chunks += 1
        if chunks > 100_000:
            raise IngestionError("RIFF contains too many chunks")
        position = end + (size & 1)
    if position < declared and declared - position > 1:
        raise IngestionError("RIFF has malformed trailing chunk bytes")


def _wav(data: bytes) -> list[ExtractedMetadata]:
    fmt: bytes | None = None
    data_bytes = 0
    for kind, payload in _riff_chunks(data, b"WAVE"):
        if kind == b"fmt " and fmt is None:
            fmt = payload
        elif kind == b"data":
            data_bytes += len(payload)
    if fmt is None or len(fmt) < 16 or not data_bytes:
        raise IngestionError("WAV is missing a bounded fmt or data chunk")
    codec, channels, sample_rate, byte_rate, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if not channels or not sample_rate or not byte_rate:
        raise IngestionError("WAV declares invalid audio parameters")
    return [
        _meta("codec_tag", codec, "audio:0"),
        _meta("channels", channels, "audio:0"),
        _meta("sample_rate_hz", sample_rate, "audio:0"),
        _meta("bits_per_sample", bits, "audio:0"),
        _meta("duration_seconds", data_bytes / byte_rate, "time:0-end"),
    ]


def _flac(data: bytes) -> list[ExtractedMetadata]:
    if len(data) < 42 or not data.startswith(b"fLaC"):
        raise IngestionError("FLAC content does not have a valid signature/STREAMINFO")
    header = data[4]
    block_type = header & 0x7F
    length = int.from_bytes(data[5:8], "big")
    if block_type != 0 or length != 34 or 8 + length > len(data):
        raise IngestionError("FLAC first metadata block is not valid STREAMINFO")
    stream = data[8:42]
    packed = int.from_bytes(stream[10:18], "big")
    sample_rate = (packed >> 44) & 0xFFFFF
    channels = ((packed >> 41) & 0x7) + 1
    bits = ((packed >> 36) & 0x1F) + 1
    samples = packed & 0xFFFFFFFFF
    if not sample_rate:
        raise IngestionError("FLAC STREAMINFO declares a zero sample rate")
    return [
        _meta("channels", channels, "audio:0"),
        _meta("sample_rate_hz", sample_rate, "audio:0"),
        _meta("bits_per_sample", bits, "audio:0"),
        _meta("total_samples", samples, "audio:0"),
        _meta("duration_seconds", samples / sample_rate, "time:0-end"),
    ]


def _mp3(data: bytes) -> list[ExtractedMetadata]:
    position = 0
    if data.startswith(b"ID3"):
        if len(data) < 10 or any(byte & 0x80 for byte in data[6:10]):
            raise IngestionError("MP3 has a malformed ID3 header")
        tag_size = sum(data[6 + index] << (21 - index * 7) for index in range(4))
        position = 10 + tag_size
        if position >= len(data):
            raise IngestionError("MP3 ID3 tag exceeds or consumes the source bytes")
    scan_end = min(len(data) - 4, position + 1024 * 1024)
    header_position = -1
    header = 0
    for candidate in range(position, scan_end + 1):
        value = int.from_bytes(data[candidate : candidate + 4], "big")
        if value & 0xFFE00000 == 0xFFE00000 and (value >> 17) & 0x3 != 0:
            header_position, header = candidate, value
            break
    if header_position < 0:
        raise IngestionError("MP3 contains no bounded MPEG audio frame header")
    version_bits = (header >> 19) & 0x3
    layer_bits = (header >> 17) & 0x3
    bitrate_index = (header >> 12) & 0xF
    sample_index = (header >> 10) & 0x3
    channel_mode = (header >> 6) & 0x3
    if version_bits == 1 or layer_bits == 0 or bitrate_index in {0, 15} or sample_index == 3:
        raise IngestionError("MP3 MPEG frame header uses invalid parameters")
    version = {3: 1.0, 2: 2.0, 0: 2.5}[version_bits]
    sample_rate = [44100, 48000, 32000][sample_index]
    if version == 2.0:
        sample_rate //= 2
    elif version == 2.5:
        sample_rate //= 4
    # A bounded first-frame estimate only; variable bitrate is intentionally not claimed exact.
    bitrate_table = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    bitrate = bitrate_table[bitrate_index]
    return [
        _meta("mpeg_version", version, "audio:0"),
        _meta("layer_bits", layer_bits, "audio:0"),
        _meta("sample_rate_hz", sample_rate, "audio:0"),
        _meta("channels", 1 if channel_mode == 3 else 2, "audio:0"),
        _meta("first_frame_bitrate_kbps", bitrate, f"bytes:{header_position}-{header_position + 3}"),
    ]


def _ogg(data: bytes) -> list[ExtractedMetadata]:
    if len(data) < 27 or not data.startswith(b"OggS") or data[4] != 0:
        raise IngestionError("Ogg content does not have a valid first-page header")
    segments = data[26]
    if 27 + segments > len(data):
        raise IngestionError("Ogg segment table is truncated")
    body_size = sum(data[27 : 27 + segments])
    body_start = 27 + segments
    if body_start + body_size > len(data):
        raise IngestionError("Ogg first page body is truncated")
    body = data[body_start : body_start + body_size]
    if body.startswith(b"OpusHead") and len(body) >= 19:
        codec = "opus"
        channels = body[9]
        sample_rate = int.from_bytes(body[12:16], "little") or 48_000
    elif body.startswith(b"\x01vorbis") and len(body) >= 16:
        codec = "vorbis"
        channels = body[11]
        sample_rate = int.from_bytes(body[12:16], "little")
    else:
        codec = "unknown-ogg-codec"
        channels = sample_rate = 0
    values = [_meta("codec", codec, "audio:0")]
    if channels:
        values.append(_meta("channels", channels, "audio:0"))
    if sample_rate:
        values.append(_meta("sample_rate_hz", sample_rate, "audio:0"))
    return values


def _iter_iso_boxes(
    data: bytes, start: int, end: int, *, depth: int = 0
) -> Iterator[tuple[bytes, int, int, bytes]]:
    if depth > 8:
        raise IngestionError("ISO-BMFF box nesting exceeds the depth limit")
    position = start
    boxes = 0
    while position < end:
        if position + 8 > end:
            raise IngestionError("ISO-BMFF contains a truncated box header")
        size = int.from_bytes(data[position : position + 4], "big")
        kind = data[position + 4 : position + 8]
        header = 8
        if size == 1:
            if position + 16 > end:
                raise IngestionError("ISO-BMFF extended-size box is truncated")
            size = int.from_bytes(data[position + 8 : position + 16], "big")
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            raise IngestionError("ISO-BMFF box exceeds its parent bounds")
        payload_start = position + header
        payload_end = position + size
        yield kind, payload_start, payload_end, data[payload_start:payload_end]
        boxes += 1
        if boxes > 100_000:
            raise IngestionError("ISO-BMFF contains too many boxes")
        position = payload_end


def _iso_bmff(data: bytes) -> list[ExtractedMetadata]:
    top = list(_iter_iso_boxes(data, 0, len(data)))
    ftyp = next((box for box in top if box[0] == b"ftyp"), None)
    if ftyp is None or len(ftyp[3]) < 8:
        raise IngestionError("ISO-BMFF content is missing a valid ftyp box")
    major_brand = ftyp[3][:4].decode("ascii", errors="replace")
    metadata = [_meta("major_brand", major_brand, f"bytes:{ftyp[1]}-{ftyp[1] + 3}")]
    containers = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}

    def inspect(boxes: Iterable[tuple[bytes, int, int, bytes]], depth: int = 0) -> None:
        for kind, payload_start, payload_end, payload in boxes:
            if kind == b"mvhd" and len(payload) >= 20:
                version = payload[0]
                if version == 0 and len(payload) >= 20:
                    timescale = int.from_bytes(payload[12:16], "big")
                    duration = int.from_bytes(payload[16:20], "big")
                elif version == 1 and len(payload) >= 32:
                    timescale = int.from_bytes(payload[20:24], "big")
                    duration = int.from_bytes(payload[24:32], "big")
                else:
                    timescale = duration = 0
                if timescale:
                    metadata.append(_meta("duration_seconds", duration / timescale, "time:0-end"))
            elif kind == b"tkhd" and len(payload) >= 8:
                width = int.from_bytes(payload[-8:-4], "big") >> 16
                height = int.from_bytes(payload[-4:], "big") >> 16
                if width and height:
                    _validate_dimensions(width, height)
                    metadata.append(_meta("track_width", width, "video:track"))
                    metadata.append(_meta("track_height", height, "video:track"))
            if kind in containers:
                inspect(_iter_iso_boxes(data, payload_start, payload_end, depth=depth + 1), depth + 1)

    inspect(top)
    return metadata


def _avi(data: bytes) -> list[ExtractedMetadata]:
    list(_riff_chunks(data, b"AVI "))
    position = data.find(b"avih", 12)
    if position < 0 or position + 8 > len(data):
        raise IngestionError("AVI is missing its main header")
    size = int.from_bytes(data[position + 4 : position + 8], "little")
    payload = data[position + 8 : position + 8 + size]
    if len(payload) < 40:
        raise IngestionError("AVI main header is truncated")
    microseconds = int.from_bytes(payload[0:4], "little")
    frames = int.from_bytes(payload[16:20], "little")
    width = int.from_bytes(payload[32:36], "little")
    height = int.from_bytes(payload[36:40], "little")
    _validate_dimensions(width, height)
    values = [_meta("width", width, "video:track"), _meta("height", height, "video:track")]
    if microseconds and frames:
        values.append(_meta("duration_seconds", microseconds * frames / 1_000_000, "time:0-end"))
    return values


def _ebml(data: bytes) -> list[ExtractedMetadata]:
    if len(data) < 8 or not data.startswith(b"\x1aE\xdf\xa3"):
        raise IngestionError("WebM/Matroska content does not have an EBML header")
    first_size = data[4]
    if first_size == 0 or first_size == 0xFF:
        raise IngestionError("EBML header has an invalid size descriptor")
    return [_meta("container", "ebml", "bytes:0-3")]


def _ffprobe(data: bytes, suffix: str) -> tuple[list[ExtractedMetadata], str]:
    executable = shutil.which("ffprobe")
    if not executable:
        return [], "ffprobe was not available; only bounded container metadata is recorded."
    resolved = Path(executable).resolve()
    if not resolved.is_file():
        return [], "ffprobe path did not resolve to a local file; it was not executed."
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() in {"systemroot", "windir", "temp", "tmp"}
    }
    environment["AV_LOG_FORCE_NOCOLOR"] = "1"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryDirectory(prefix="amca-ffprobe-") as directory:
        source = Path(directory) / f"source{suffix}"
        source.write_bytes(data)
        command = [
            str(resolved), "-v", "error", "-nostdin", "-protocol_whitelist", "file",
            "-probesize", "5000000", "-analyzeduration", "5000000",
            "-show_entries",
            "format=format_name,duration:stream=index,codec_type,codec_name,width,height,duration,sample_rate,channels",
            "-of", "json", str(source),
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=FFPROBE_TIMEOUT_SECONDS,
                check=False,
                shell=False,
                env=environment,
                creationflags=flags,
            )
        except (OSError, subprocess.TimeoutExpired):
            return [], "The bounded ffprobe metadata pass failed or timed out."
    if (
        completed.returncode != 0
        or len(completed.stdout) > MAX_FFPROBE_OUTPUT_BYTES
        or len(completed.stderr) > MAX_FFPROBE_OUTPUT_BYTES
    ):
        return [], "The bounded ffprobe metadata pass rejected the media."
    try:
        parsed = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], "ffprobe returned invalid or non-UTF-8 metadata."
    metadata: list[ExtractedMetadata] = []
    streams = parsed.get("streams", []) if isinstance(parsed, dict) else []
    if isinstance(streams, list) and len(streams) <= 256:
        for stream_index, stream in enumerate(streams):
            if not isinstance(stream, dict):
                continue
            locator = f"stream:{stream.get('index', stream_index)}"
            for key in (
                "codec_type", "codec_name", "width", "height", "duration",
                "sample_rate", "channels",
            ):
                value = stream.get(key)
                if isinstance(value, (str, int, float)):
                    metadata.append(_meta(f"ffprobe.stream.{stream_index}.{key}", value, locator))
    format_data = parsed.get("format", {}) if isinstance(parsed, dict) else {}
    if isinstance(format_data, dict):
        for key in ("format_name", "duration"):
            value = format_data.get(key)
            if isinstance(value, (str, int, float)):
                metadata.append(_meta(f"ffprobe.format.{key}", value, "container"))
    return metadata, "A fixed, local, timeout-bounded ffprobe metadata pass also succeeded."


def _media(
    suffix: str,
    data: bytes,
    *,
    video: bool,
    media_tools_root: str | os.PathLike[str] | None = None,
    enrich: bool = True,
) -> IngestionResult:
    if suffix == ".wav":
        media_type, metadata = "audio/wav", _wav(data)
    elif suffix == ".flac":
        media_type, metadata = "audio/flac", _flac(data)
    elif suffix == ".mp3":
        media_type, metadata = "audio/mpeg", _mp3(data)
    elif suffix in {".ogg", ".oga", ".ogv"}:
        media_type = "video/ogg" if suffix == ".ogv" else "audio/ogg"
        metadata = _ogg(data)
    elif suffix in {".m4a", ".mp4", ".m4v", ".mov"}:
        media_type = {
            ".m4a": "audio/mp4", ".mp4": "video/mp4",
            ".m4v": "video/x-m4v", ".mov": "video/quicktime",
        }[suffix]
        metadata = _iso_bmff(data)
    elif suffix == ".avi":
        media_type, metadata = "video/x-msvideo", _avi(data)
    elif suffix in {".webm", ".mkv"}:
        media_type = "video/webm" if suffix == ".webm" else "video/x-matroska"
        metadata = _ebml(data)
    else:  # pragma: no cover - dispatch prevents this
        raise IngestionError("unsupported media suffix")
    probed, probe_note = _ffprobe(data, suffix)
    metadata.extend(probed)
    extraction = (
        transcribe_audio(data, suffix=suffix, tools_root=media_tools_root)
        if enrich and not video
        else None
    )
    builder = _ChunkBuilder()
    if extraction is not None:
        for chunk in extraction.chunks:
            builder.add(chunk.locator, chunk.text)
    if video:
        enrichment_note = (
            "AMCA did not extract an audio track, transcribe speech, decode frames, detect scenes, or claim semantic content."
        )
        status = "metadata_only"
        method = "bounded-media-container-v1" + ("+ffprobe" if probed else "")
    elif extraction is None:
        enrichment_note = "Semantic enrichment was not run during validation."
        status = "metadata_only"
        method = "bounded-media-container-v1" + ("+ffprobe" if probed else "")
    else:
        enrichment_note = extraction.note
        status = extraction.status
        method = extraction.method + ("+ffprobe" if probed else "")
    return IngestionResult(
        media_type=media_type,
        modality="video" if video else "audio",
        status=status,
        method=method,
        note=(
            "Source/container metadata is preserved. "
            + enrichment_note
            + " "
            + probe_note
        ),
        chunks=builder.finish(),
        metadata=tuple(metadata),
    )


def _pdf(data: bytes) -> IngestionResult:
    if not data.startswith(b"%PDF-"):
        raise IngestionError("PDF content does not have a valid PDF header")
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - declared core dependency
        raise IngestionError("PDF extraction requires the declared pypdf dependency") from error
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
    except Exception as error:
        raise IngestionError(f"PDF could not be parsed: {type(error).__name__}") from error
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise IngestionError("encrypted PDFs require explicit decryption before import")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise IngestionError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit")
    builder = _ChunkBuilder()
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raise IngestionError(f"PDF page {page_number} text extraction failed") from error
        builder.add(f"page:{page_number}", text, include_empty=True)
    chunks = builder.finish()
    has_text = any(chunk.text for chunk in chunks)
    return IngestionResult(
        media_type="application/pdf",
        modality="document",
        status="text_extracted" if has_text else "metadata_only",
        method="pypdf-bounded-pages-v1",
        note=(
            "Page text is extracted locally; empty pages may be scans. Images, drawings, "
            "tables, OCR, annotations, and layout semantics are not claimed."
        ),
        chunks=chunks,
        metadata=(_meta("page_count", len(reader.pages), "document"),),
    )


def extract_asset(
    filename: str,
    data: bytes,
    *,
    media_tools_root: str | os.PathLike[str] | None = None,
    enrich: bool = True,
) -> IngestionResult:
    """Detect from a safe filename suffix, then validate the matching byte format."""

    suffix = Path(filename).suffix.casefold()
    text_like = {
        ".txt": ("text/plain", "document"),
        ".csv": ("text/csv", "spreadsheet"),
        ".tsv": ("text/tab-separated-values", "spreadsheet"),
        ".json": ("application/json", "document"),
        ".xml": ("application/xml", "document"),
        ".rtf": ("application/rtf", "document"),
        ".tex": ("application/x-tex", "document"),
        ".svg": ("image/svg+xml", "image"),
        ".py": ("text/x-python", "document"),
        ".js": ("text/javascript", "document"),
        ".jsx": ("text/jsx", "document"),
        ".ts": ("text/typescript", "document"),
        ".tsx": ("text/tsx", "document"),
        ".java": ("text/x-java", "document"),
        ".c": ("text/x-c", "document"),
        ".h": ("text/x-c", "document"),
        ".cpp": ("text/x-c++", "document"),
        ".hpp": ("text/x-c++", "document"),
        ".cs": ("text/x-csharp", "document"),
        ".go": ("text/x-go", "document"),
        ".rs": ("text/x-rust", "document"),
        ".rb": ("text/x-ruby", "document"),
        ".php": ("text/x-php", "document"),
        ".sql": ("application/sql", "document"),
        ".yaml": ("application/yaml", "document"),
        ".yml": ("application/yaml", "document"),
        ".toml": ("application/toml", "document"),
        ".ini": ("text/plain", "document"),
        ".sh": ("text/x-shellscript", "document"),
        ".ps1": ("text/plain", "document"),
        ".bat": ("text/plain", "document"),
    }
    if suffix in text_like:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise IngestionError(f"{suffix or 'text'} assets must be valid UTF-8") from error
        if "\x00" in text:
            raise IngestionError("text assets cannot contain NUL bytes")
        if suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as error:
                raise IngestionError("JSON asset is malformed") from error
        if suffix in {".xml", ".svg"} and _XML_FORBIDDEN.search(data):
            raise IngestionError("XML/SVG assets cannot contain DTD or entity declarations")
        builder = _ChunkBuilder()
        builder.extend_text(text)
        media_type, modality = text_like[suffix]
        return IngestionResult(
            media_type, modality, "text_extracted", "utf8-lines-v1",
            "UTF-8 source was normalized to LF and chunked with exact line ranges; it was never executed.",
            builder.finish(), (),
        )
    if suffix in {".md", ".markdown"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise IngestionError("Markdown assets must be valid UTF-8") from error
        builder = _ChunkBuilder()
        builder.extend_text(text)
        return IngestionResult(
            "text/markdown", "document", "text_extracted", "utf8-lines-v1",
            "UTF-8 Markdown source was preserved as text; embedded resources were not fetched.",
            builder.finish(), (),
        )
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".docx":
        return _docx(data)
    if suffix == ".xlsx":
        return _xlsx(data)
    if suffix == ".pptx":
        return _pptx(data)
    if suffix in {".html", ".htm"}:
        return _html(data)
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
        return _image(
            suffix,
            data,
            media_tools_root=media_tools_root,
            enrich=enrich,
        )
    if suffix in {".wav", ".flac", ".mp3", ".ogg", ".oga", ".m4a"}:
        return _media(
            suffix,
            data,
            video=False,
            media_tools_root=media_tools_root,
            enrich=enrich,
        )
    if suffix in {".mp4", ".m4v", ".mov", ".avi", ".webm", ".mkv", ".ogv"}:
        return _media(
            suffix,
            data,
            video=True,
            media_tools_root=media_tools_root,
            enrich=enrich,
        )
    raise IngestionError(
        "ingestion accepts TXT/Markdown/PDF, DOCX, XLSX, PPTX, HTML, common raster "
        "images, and bounded audio/video containers"
    )


__all__ = [
    "ExtractedChunk",
    "ExtractedMetadata",
    "IngestionError",
    "IngestionResult",
    "MAX_ASSET_BYTES",
    "MAX_EXTRACTED_CHARACTERS",
    "MAX_PDF_PAGES",
    "extract_asset",
]
