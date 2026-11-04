"""Full Access must mean the whole PC, and a relative path means "in the project".

Two defects this file pins:

1. Under Full Computer the granted roots are every fixed drive, so a
   RELATIVE path had several possible anchors and the first granted drive
   won.  The agent therefore searched a mapped cloud drive while the operator
   had pointed the Project at a source folder - and the tool contract says
   "Project-relative path".

2. A Project's folder is its workspace root, and the workspace root is what
   the agent's relative paths mean.  The resolution now anchors a relative
   path there, and the executor receives the path relative to the grant that
   was selected, so the same path always lands on the same file.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.local_tools import (  # noqa: E402
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PROFILE_CAPABILITIES,
    PermissionProfile,
)

READ_CAPS = [
    FileCapability.LIST,
    FileCapability.STAT,
    FileCapability.SEARCH,
    FileCapability.READ,
]


def _store(tmp_path: Path, project_id: str) -> LocalPermissionStore:
    """A permission store whose database also knows the Project.

    The store refuses a level for an id that names no real chat or Project,
    so the Project row has to exist before a profile can be set.
    """

    database = tmp_path / f"local-tools-{project_id}.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS studio_projects (
                   project_id TEXT PRIMARY KEY,
                   name TEXT NOT NULL,
                   description TEXT,
                   instructions TEXT,
                   template TEXT,
                   created_at TEXT,
                   updated_at TEXT,
                   archived INTEGER NOT NULL DEFAULT 0,
                   root_path TEXT
               )"""
        )
        connection.execute(
            "INSERT OR REPLACE INTO studio_projects (project_id, name) VALUES (?, ?)",
            (project_id, project_id),
        )
    return LocalPermissionStore(database)


def test_a_relative_path_is_anchored_at_the_project_folder(tmp_path):
    """The Project folder is the anchor, not whichever drive was granted."""

    store = _store(tmp_path, "studio-1")
    project = tmp_path / "project"
    project.mkdir()
    (project / "marker.txt").write_text("inside the project", encoding="utf-8")
    (tmp_path / "elsewhere.txt").write_text("outside the project", encoding="utf-8")

    store.set_profile(
        project_id="studio-1",
        profile=PermissionProfile.FULL_COMPUTER,
        workspace_root=str(project),
    )
    grant = store.create_grant(
        project_id="studio-1",
        root_path=str(tmp_path),
        capabilities=READ_CAPS,
        reason="full computer drive grant",
        ttl_seconds=3600,
        created_by="desktop_user",
    )

    executor = LocalToolExecutor(store, state_root=tmp_path / "state")
    resolution = executor.resolve_mutation_target(
        project_id="studio-1", action=FileCapability.READ, path="marker.txt",
    )
    assert resolution["matched"], resolution
    assert resolution["grant_id"] == grant.grant_id
    # Resolved against the grant, the path must land on the PROJECT's file -
    # not on the same-named file that sits next to the grant root.
    root = Path(str(grant.canonical_root))
    target = root / str(resolution["relative_path"]).replace("/", "\\")
    assert target.read_text(encoding="utf-8") == "inside the project"


def test_an_absolute_path_still_resolves_to_its_own_grant(tmp_path):
    """An absolute path is never re-anchored at the project folder."""

    store = _store(tmp_path, "studio-2")
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    target = elsewhere / "note.txt"
    target.write_text("elsewhere", encoding="utf-8")

    store.set_profile(
        project_id="studio-2",
        profile=PermissionProfile.FULL_COMPUTER,
        workspace_root=str(project),
    )
    grant = store.create_grant(
        project_id="studio-2",
        root_path=str(tmp_path),
        capabilities=READ_CAPS,
        reason="full computer drive grant",
        ttl_seconds=3600,
        created_by="desktop_user",
    )

    executor = LocalToolExecutor(store, state_root=tmp_path / "state")
    resolution = executor.resolve_mutation_target(
        project_id="studio-2", action=FileCapability.READ, path=str(target),
    )
    assert resolution["matched"], resolution
    assert resolution["grant_id"] == grant.grant_id
    root = Path(str(grant.canonical_root))
    landed = root / str(resolution["relative_path"]).replace("/", "\\")
    assert landed.read_text(encoding="utf-8") == "elsewhere"


def test_the_workspace_root_is_reported_verbatim(tmp_path):
    """The Project folder the operator set IS the workspace root."""

    store = _store(tmp_path, "studio-3")
    folder = tmp_path / "amca-dev-source"
    folder.mkdir()
    store.set_profile(
        project_id="studio-3",
        profile=PermissionProfile.FULL_COMPUTER,
        workspace_root=str(folder),
    )
    assert store.profile("studio-3")["workspace_root"] == str(folder)


def test_full_computer_grants_every_capability_and_the_levels_differ():
    """Full Access is the whole PC, and the levels below it are real steps.

    The Permissions screen shows one folder-grant form for every level, so
    the capability checkboxes it offers have to be the level's own set -
    otherwise every level looks identical.
    """

    full = PROFILE_CAPABILITIES[PermissionProfile.FULL_COMPUTER]
    restricted = PROFILE_CAPABILITIES[PermissionProfile.RESTRICTED]
    standard = PROFILE_CAPABILITIES[PermissionProfile.STANDARD]
    extended = PROFILE_CAPABILITIES[PermissionProfile.EXTENDED]
    assert full == frozenset(FileCapability)
    assert restricted < standard < extended < full
    assert FileCapability.WRITE not in restricted
    assert FileCapability.WRITE in standard
    assert FileCapability.PROPOSE_COMMAND not in standard
    assert FileCapability.PROPOSE_COMMAND in extended
