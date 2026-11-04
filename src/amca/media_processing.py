"""Optional, bounded local OCR and speech-to-text processors.

The processors are deliberately executable allow-lists rather than arbitrary
commands.  They never use a network connection: Tesseract reads one validated
image and whisper.cpp reads one validated audio file plus a model stored under
the AMCA application-data directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MAX_LOCAL_TEXT_BYTES = 4 * 1024 * 1024
MAX_LOCAL_DIAGNOSTIC_BYTES = 256 * 1024
OCR_TIMEOUT_SECONDS = 45
TRANSCRIPTION_TIMEOUT_SECONDS = 180
WHISPER_MODEL_NAME = "ggml-tiny.en.bin"
_NON_SPEECH_MARKERS = {
    "[blank_audio]",
    "[no speech]",
    "[silence]",
}


@dataclass(frozen=True, slots=True)
class LocalTextChunk:
    locator: str
    text: str


@dataclass(frozen=True, slots=True)
class LocalMediaExtraction:
    status: str
    method: str
    note: str
    chunks: tuple[LocalTextChunk, ...]


@dataclass(frozen=True, slots=True)
class LocalMediaRuntimeStatus:
    ocr_available: bool
    ocr_runtime: str | None
    transcription_available: bool
    transcription_runtime: str | None
    transcription_model: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ocr": {
                "available": self.ocr_available,
                "runtime": self.ocr_runtime,
                "scope": "validated raster images; English printed text",
            },
            "transcription": {
                "available": self.transcription_available,
                "runtime": self.transcription_runtime,
                "model": self.transcription_model,
                "scope": "FLAC, MP3, Ogg, or WAV; 25 MB input; 180-second local CPU limit",
            },
            "network_used": False,
        }


def _safe_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() in {"systemroot", "windir", "temp", "tmp"}
    }
    if extra:
        environment.update(extra)
    return environment


def _existing_executable(candidates: tuple[Path | None, ...], name: str) -> Path | None:
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_file() and resolved.name.casefold() == name.casefold():
            return resolved
    return None


def _tesseract_executable(tools_root: Path | None) -> Path | None:
    path_match = shutil.which("tesseract")
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    return _existing_executable(
        (
            tools_root / "tesseract" / "tesseract.exe" if tools_root else None,
            Path(path_match) if path_match else None,
            Path(program_files) / "Tesseract-OCR" / "tesseract.exe"
            if program_files else None,
            Path(program_files_x86) / "Tesseract-OCR" / "tesseract.exe"
            if program_files_x86 else None,
        ),
        "tesseract.exe" if os.name == "nt" else "tesseract",
    )


def _whisper_files(tools_root: Path | None) -> tuple[Path | None, Path | None]:
    if tools_root is None:
        return None, None
    directory = tools_root / "whisper"
    executable_name = "whisper-cli.exe" if os.name == "nt" else "whisper-cli"
    executable = _existing_executable((directory / executable_name,), executable_name)
    model = directory / WHISPER_MODEL_NAME
    try:
        resolved_model = model.resolve(strict=True)
    except (OSError, RuntimeError):
        resolved_model = None
    if resolved_model is None or not resolved_model.is_file():
        return executable, None
    return executable, resolved_model


def local_media_runtime_status(
    tools_root: str | os.PathLike[str] | None,
) -> LocalMediaRuntimeStatus:
    root = Path(tools_root).resolve() if tools_root is not None else None
    tesseract = _tesseract_executable(root)
    whisper, model = _whisper_files(root)
    return LocalMediaRuntimeStatus(
        ocr_available=tesseract is not None,
        ocr_runtime="Tesseract" if tesseract is not None else None,
        transcription_available=whisper is not None and model is not None,
        transcription_runtime="whisper.cpp" if whisper is not None else None,
        transcription_model=WHISPER_MODEL_NAME if model is not None else None,
    )


def _run(
    command: list[str],
    *,
    timeout: int,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes] | None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
            env=_safe_environment(environment),
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if (
        len(completed.stdout) > MAX_LOCAL_TEXT_BYTES
        or len(completed.stderr) > MAX_LOCAL_DIAGNOSTIC_BYTES
    ):
        return None
    return completed


def ocr_image(
    data: bytes,
    *,
    suffix: str,
    tools_root: str | os.PathLike[str] | None,
) -> LocalMediaExtraction:
    root = Path(tools_root).resolve() if tools_root is not None else None
    executable = _tesseract_executable(root)
    if executable is None:
        return LocalMediaExtraction(
            "metadata_only",
            "bounded-raster-header-v1",
            "Local OCR is unavailable because Tesseract was not found.",
            (),
        )
    tessdata = executable.parent / "tessdata"
    extra = {"TESSDATA_PREFIX": str(tessdata)} if tessdata.is_dir() else None
    with tempfile.TemporaryDirectory(prefix="amca-ocr-") as directory:
        source = Path(directory) / f"source{suffix}"
        source.write_bytes(data)
        completed = _run(
            [
                str(executable),
                str(source),
                "stdout",
                "-l",
                "eng",
                "--psm",
                "6",
            ],
            timeout=OCR_TIMEOUT_SECONDS,
            environment=extra,
        )
    if completed is None or completed.returncode != 0:
        return LocalMediaExtraction(
            "metadata_only",
            "bounded-raster-header-v1+tesseract-failed",
            "Tesseract failed, timed out, or exceeded the bounded output limit; no OCR text is claimed.",
            (),
        )
    try:
        text = completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        text = ""
    if not text:
        return LocalMediaExtraction(
            "metadata_only",
            "bounded-raster-header-v1+tesseract-empty",
            "Tesseract completed locally but found no English printed text.",
            (),
        )
    return LocalMediaExtraction(
        "ocr_extracted",
        "bounded-raster-header-v1+tesseract-eng-v1",
        "English printed text was extracted locally with Tesseract; handwriting, layout, objects, and visual meaning are not claimed.",
        (LocalTextChunk("image:1/ocr:eng", text),),
    )


def _timestamp(value: object, fallback: str) -> str:
    rendered = str(value).strip()
    if not rendered or len(rendered) > 32 or any(
        character not in "0123456789:,." for character in rendered
    ):
        return fallback
    return rendered.replace(",", ".")


def _transcript_chunks(value: object) -> tuple[LocalTextChunk, ...]:
    if not isinstance(value, Mapping):
        return ()
    raw_segments = value.get("transcription")
    if not isinstance(raw_segments, list) or len(raw_segments) > 20_000:
        return ()
    chunks: list[LocalTextChunk] = []
    for index, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, Mapping):
            continue
        text = str(raw.get("text", "")).strip()
        if not text or text.casefold() in _NON_SPEECH_MARKERS:
            continue
        timestamps = raw.get("timestamps")
        if isinstance(timestamps, Mapping):
            start = _timestamp(timestamps.get("from"), str(index - 1))
            end = _timestamp(timestamps.get("to"), str(index))
        else:
            start, end = str(index - 1), str(index)
        chunks.append(LocalTextChunk(f"time:{start}-{end}", text))
    return tuple(chunks)


def transcribe_audio(
    data: bytes,
    *,
    suffix: str,
    tools_root: str | os.PathLike[str] | None,
) -> LocalMediaExtraction:
    if suffix not in {".flac", ".mp3", ".ogg", ".oga", ".wav"}:
        return LocalMediaExtraction(
            "metadata_only",
            "bounded-media-container-v1",
            "This audio container is validated but the local whisper.cpp runtime accepts only FLAC, MP3, Ogg, and WAV.",
            (),
        )
    root = Path(tools_root).resolve() if tools_root is not None else None
    executable, model = _whisper_files(root)
    if executable is None or model is None:
        return LocalMediaExtraction(
            "metadata_only",
            "bounded-media-container-v1",
            "Local transcription is unavailable because whisper.cpp and its pinned local model were not found.",
            (),
        )
    with tempfile.TemporaryDirectory(prefix="amca-whisper-") as directory:
        source = Path(directory) / f"source{suffix}"
        output = Path(directory) / "transcript"
        source.write_bytes(data)
        completed = _run(
            [
                str(executable),
                "-m",
                str(model),
                "-f",
                str(source),
                "-l",
                "auto",
                "-t",
                str(max(1, min(4, os.cpu_count() or 1))),
                "-ng",
                "-ojf",
                "-of",
                str(output),
                "-np",
            ],
            timeout=TRANSCRIPTION_TIMEOUT_SECONDS,
        )
        result_path = output.with_suffix(".json")
        if (
            completed is None
            or completed.returncode != 0
            or not result_path.is_file()
            or result_path.stat().st_size > MAX_LOCAL_TEXT_BYTES
        ):
            return LocalMediaExtraction(
                "metadata_only",
                "bounded-media-container-v1+whisper-failed",
                "whisper.cpp failed, timed out, or exceeded a bounded output limit; no transcript is claimed.",
                (),
            )
        try:
            parsed = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
    chunks = _transcript_chunks(parsed)
    if not chunks:
        return LocalMediaExtraction(
            "metadata_only",
            "bounded-media-container-v1+whisper-empty",
            "whisper.cpp completed locally but produced no speech transcript.",
            (),
        )
    return LocalMediaExtraction(
        "transcript_extracted",
        "bounded-media-container-v1+whisper.cpp-tiny.en-v1",
        "Speech was transcribed locally with whisper.cpp tiny.en under the 25 MB input and 180-second process limits; timestamps and text may contain recognition errors.",
        chunks,
    )


__all__ = [
    "LocalMediaExtraction",
    "LocalMediaRuntimeStatus",
    "LocalTextChunk",
    "WHISPER_MODEL_NAME",
    "local_media_runtime_status",
    "ocr_image",
    "transcribe_audio",
]
