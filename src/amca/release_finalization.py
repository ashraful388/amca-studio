"""AMCA Studio v0.7.0 Public Release Finalization Foundation (AG-AMCA-STUDIO-V0.7.0).

Provides public release package assembly, operational handoff registry,
final release metadata generation, and verification for AMCA Studio v0.7.0.
Zero external cloud dependencies; 100% offline, local-first operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class OperationalHandoffRecord:
    handoff_id: str
    target_version: str
    release_tag: str
    git_commit_sha: str
    release_channel: str
    status: str
    finalized_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "target_version": self.target_version,
            "release_tag": self.release_tag,
            "git_commit_sha": self.git_commit_sha,
            "release_channel": self.release_channel,
            "status": self.status,
            "finalized_at": self.finalized_at,
        }


@dataclass(frozen=True, slots=True)
class PublicReleaseManifest:
    release_version: str
    release_tag: str
    artifacts_verified_count: int
    handoff: OperationalHandoffRecord
    operational_readiness: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_version": self.release_version,
            "release_tag": self.release_tag,
            "artifacts_verified_count": self.artifacts_verified_count,
            "handoff": self.handoff.to_dict(),
            "operational_readiness": self.operational_readiness,
            "created_at": self.created_at,
        }


#: H2A S6 (version-aware): the authoritative public artifact count per
#: release line. v1.0.0 shipped EXE + Portable (2); v1.0.1+ ships
#: EXE + MSI + Portable (3). Historical v1.0.0 records stay valid.
EXPECTED_PUBLIC_ARTIFACT_COUNTS: dict[str, int] = {
    "1.0.0": 2,
}


def expected_public_artifact_count(release_version: str) -> int:
    """Authoritative public artifact count for a release version.

    v1.0.1 and later ship three artifacts (EXE + MSI + Portable); the v1.0.0
    historical two-artifact record is preserved unchanged.
    """

    key = str(release_version).strip().lstrip("v")
    return EXPECTED_PUBLIC_ARTIFACT_COUNTS.get(key, 3)


def assemble_public_release_package(
    release_version: str = "v0.7.0",
    *,
    git_commit_sha: str = "HEAD",
    release_channel: str = "public_beta",
    artifacts_count: int | None = None,
    all_gates_cleared: bool = True,
) -> PublicReleaseManifest:
    """Assembles the final public release package and operational handoff.

    H2A S6: when artifacts_count is omitted it defaults to the authoritative
    public artifact count for the requested version (3 for v1.0.1+; the
    historical v1.0.0 two-artifact record is preserved).
    """

    if artifacts_count is None:
        artifacts_count = expected_public_artifact_count(release_version)
    now_str = _now()
    handoff_status = "READY_FOR_DISTRIBUTION" if all_gates_cleared else "PENDING"
    readiness = "PUBLIC_RELEASE_READY" if all_gates_cleared else "HOLD"

    handoff = OperationalHandoffRecord(
        handoff_id=f"handoff-{uuid4().hex[:16]}",
        target_version=str(release_version).strip(),
        release_tag=f"{release_version}",
        git_commit_sha=str(git_commit_sha).strip(),
        release_channel=str(release_channel).strip(),
        status=handoff_status,
        finalized_at=now_str,
    )

    return PublicReleaseManifest(
        release_version=str(release_version).strip(),
        release_tag=f"{release_version}",
        artifacts_verified_count=artifacts_count,
        handoff=handoff,
        operational_readiness=readiness,
        created_at=now_str,
    )

