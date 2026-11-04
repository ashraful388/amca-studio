"""Versioned project workspace files (milestone X1.2).

The workspace is AMCA-managed storage scoped to one Studio Project.  It is
deliberately NOT arbitrary disk access: every path is contained inside
``<data>/workspaces/<project>/``, traversal and drive-absolute forms are
rejected, writes keep a bounded per-file version history so any change can be
reviewed as a unified diff against its declared base version, and conflicting
concurrent edits fail closed instead of silently overwriting.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

MAX_WORKSPACE_FILE_BYTES = 512 * 1024
MAX_VERSIONS_PER_FILE = 20


class WorkspaceError(ValueError):
    pass


def _safe_relative_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise WorkspaceError("workspace path is required")
    candidate = Path(raw.strip().replace("\\", "/"))
    if candidate.is_absolute() or candidate.drive or candidate.root:
        raise WorkspaceError("workspace paths must be relative to the Project")
    parts = [part for part in candidate.parts if part not in {".", ""}]
    if not parts or any(part == ".." for part in parts):
        raise WorkspaceError("workspace path traversal is not allowed")
    return Path(*parts)


class WorkspaceStore:
    """Bounded, versioned text files owned by one Project."""

    def __init__(self, root: Path) -> None:
        # Resolve once so every derived path shares one canonical form
        # (Windows temp dirs and symlinks can otherwise produce 8.3 aliases).
        self.root = Path(root).resolve()

    def _project_dir(self, project_id: str) -> Path:
        if (
            not isinstance(project_id, str)
            or not project_id.strip()
            or len(project_id) > 100
            or any(character in project_id for character in "/\\:.")
        ):
            raise WorkspaceError("invalid project id for workspace")
        directory = self.root / project_id.strip()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def project_root(self, project_id: str) -> Path:
        """Public read accessor for one project's authorized directory."""

        return self._project_dir(project_id)

    def _file_path(self, project_id: str, raw_path: str) -> Path:
        relative = _safe_relative_path(raw_path)
        target = (self._project_dir(project_id) / relative).resolve()
        container = self._project_dir(project_id).resolve()
        if not str(target).startswith(str(container)):
            raise WorkspaceError("workspace path traversal is not allowed")
        return target

    @staticmethod
    def _meta_path(target: Path) -> Path:
        return target.with_name(f".{target.name}.versions.json")

    def _load_versions(self, target: Path) -> list[str]:
        meta = self._meta_path(target)
        if not meta.exists():
            return []
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [str(item) for item in data.get("versions", [])] if isinstance(data, dict) else []

    def _save_versions(self, target: Path, versions: list[str]) -> None:
        trimmed = versions[-MAX_VERSIONS_PER_FILE:]
        self._meta_path(target).write_text(
            json.dumps({"versions": trimmed}, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_files(self, project_id: str) -> list[dict[str, Any]]:
        directory = self._project_dir(project_id)
        files: list[dict[str, Any]] = []
        for target in sorted(directory.rglob("*")):
            if not target.is_file():
                continue
            relative = target.relative_to(directory)
            # Hidden entries are internal: version sidecars and the .history
            # archive tree never appear as workspace files.
            if any(part.startswith(".") for part in relative.parts):
                continue
            versions = self._load_versions(target)
            stat = target.stat()
            files.append(
                {
                    "path": relative.as_posix(),
                    "bytes": stat.st_size,
                    "version": len(versions) + 1,
                    "updated_at": stat.st_mtime,
                }
            )
        return files

    def read_file(self, project_id: str, raw_path: str) -> dict[str, Any]:
        target = self._file_path(project_id, raw_path)
        if not target.exists():
            raise WorkspaceError("workspace file not found")
        data = target.read_bytes()
        if len(data) > MAX_WORKSPACE_FILE_BYTES:
            raise WorkspaceError("workspace file exceeds the size limit")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("workspace files must be UTF-8 text") from exc
        return {
            "path": target.relative_to(self._project_dir(project_id)).as_posix(),
            "content": content,
            "version": len(self._load_versions(target)) + 1,
        }

    def read_version(self, project_id: str, raw_path: str, version: int) -> str:
        target = self._file_path(project_id, raw_path)
        versions = self._load_versions(target)
        if version < 1 or version > len(versions) + 1:
            raise WorkspaceError("unknown workspace file version")
        source = versions[version - 1] if version <= len(versions) else None
        if source is None:
            return target.read_text(encoding="utf-8")
        archive = self._project_dir(project_id) / ".history" / target.relative_to(
            self._project_dir(project_id)
        ).with_name(f"{target.name}.{version}")
        return archive.read_text(encoding="utf-8")

    def write_file(
        self,
        project_id: str,
        raw_path: str,
        content: str,
        *,
        base_version: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(content, str):
            raise WorkspaceError("workspace content must be text")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WORKSPACE_FILE_BYTES:
            raise WorkspaceError("workspace file exceeds the size limit")
        target = self._file_path(project_id, raw_path)
        existed = target.exists()
        previous_version = len(self._load_versions(target)) + 1 if existed else 0
        if existed and base_version is not None and base_version != previous_version:
            raise WorkspaceError(
                f"workspace file changed since version {base_version}; "
                f"current version is {previous_version}"
            )
        base_content = (
            target.read_text(encoding="utf-8")
            if existed
            else ""
        )
        if existed:
            history_dir = self._project_dir(project_id) / ".history" / target.relative_to(
                self._project_dir(project_id)
            ).parent
            history_dir.mkdir(parents=True, exist_ok=True)
            archive = history_dir / f"{target.name}.{previous_version}"
            archive.write_text(base_content, encoding="utf-8", newline="")
            versions = self._load_versions(target)
            versions.append(base_content)
            self._save_versions(target, versions)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        new_version = len(self._load_versions(target)) + 1
        diff = "\n".join(
            difflib.unified_diff(
                base_content.splitlines(),
                content.splitlines(),
                fromfile=f"a/{raw_path} (v{previous_version})",
                tofile=f"b/{raw_path} (v{new_version})",
                lineterm="",
            )
        )
        return {
            "path": target.relative_to(self._project_dir(project_id)).as_posix(),
            "version": new_version,
            "created": not existed,
            "diff": diff,
        }

    def _archive_path(self, project_id: str, target: Path, version: int) -> Path:
        return (
            self._project_dir(project_id)
            / ".history"
            / target.relative_to(self._project_dir(project_id))
        ).with_name(f"{target.name}.{version}")

    def revert_file(
        self, project_id: str, raw_path: str, version: int
    ) -> dict[str, Any]:
        """Restore an old version's content as a NEW version (history intact)."""

        current = self.read_file(project_id, raw_path)
        restored = self.read_version(project_id, raw_path, version)
        if restored == current["content"]:
            raise WorkspaceError("version already matches the current content")
        return self.write_file(project_id, raw_path, restored)

    def recent_changes(
        self, project_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        directory = self._project_dir(project_id)
        entries: list[dict[str, Any]] = []
        for target in sorted(directory.rglob("*")):
            if not target.is_file():
                continue
            relative = target.relative_to(directory)
            if any(part.startswith(".") for part in relative.parts):
                continue
            versions = self._load_versions(target)
            total = len(versions) + 1
            rel_posix = relative.as_posix()
            for version in range(2, total + 1):
                archive = self._archive_path(project_id, target, version - 1)
                try:
                    previous = (
                        archive.read_text(encoding="utf-8")
                        if archive.exists()
                        else ""
                    )
                except OSError:
                    continue
                current_content = (
                    target.read_text(encoding="utf-8")
                    if version == total
                    else None
                )
                if current_content is None:
                    older_archive = self._archive_path(project_id, target, version)
                    current_content = older_archive.read_text(encoding="utf-8")
                stat = (
                    target.stat()
                    if version == total
                    else archive.stat()
                )
                diff = "\n".join(
                    difflib.unified_diff(
                        previous.splitlines(),
                        current_content.splitlines(),
                        fromfile=f"a/{rel_posix} (v{version - 1})",
                        tofile=f"b/{rel_posix} (v{version})",
                        lineterm="",
                    )
                )
                entries.append(
                    {
                        "path": rel_posix,
                        "version": version,
                        "saved_at": stat.st_mtime,
                        "diff": diff,
                    }
                )
        entries.sort(key=lambda item: item["saved_at"], reverse=True)
        return entries[: max(1, int(limit))]

    def delete_file(self, project_id: str, raw_path: str) -> dict[str, Any]:
        target = self._file_path(project_id, raw_path)
        if not target.exists():
            raise WorkspaceError("workspace file not found")
        target.unlink()
        meta = self._meta_path(target)
        if meta.exists():
            meta.unlink()
        return {"deleted": target.relative_to(self._project_dir(project_id)).as_posix()}


__all__ = ["WorkspaceError", "WorkspaceStore"]
