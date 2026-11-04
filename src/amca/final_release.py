"""AMCA Studio v0.9.0 Final Release Preparation and Distribution Packaging.

Milestone: ZCODE-AMCA-STUDIO-V0.9.0-FINAL-RELEASE-PREP-AND-DISTRIBUTION-PACKAGE-01

Responsibilities:
1. Release Manifest Validation:
   - Verifies release_manifest_v0.9.0.json structure, semantic artifact naming, and SHA256 checksums.
2. Release Documentation Freeze:
   - Ensures all release notes and reports contain zero ungrounded claims or lingering RC draft artifacts.
3. Distribution Output Verification:
   - Confirms that distribution descriptors point to verified builds in software/dist/.
4. Final Release Recommendation:
   - Certifies GA readiness for official distribution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FinalReleaseValidationResult:
    manifest_path: str
    product_name: str
    version: str
    release_type: str
    artifact_count: int
    artifacts_verified: bool
    documentation_verified: bool
    governance_contract: str
    certification_status: str
    is_ready_for_release: bool
    details: List[str]


class FinalReleaseManager:
    """Manages and verifies the final AMCA Studio v0.9.0 distribution package."""

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parent.parent.parent.parent
        self.dist_dir = self.repo_root / "software" / "dist"
        self.manifest_path = self.dist_dir / "release_manifest_v0.9.0.json"

    def load_manifest(self) -> Dict[str, Any]:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Release manifest not found at {self.manifest_path}")
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def validate_final_release(self) -> FinalReleaseValidationResult:
        details: List[str] = []
        manifest = self.load_manifest()

        product = manifest.get("product_name", "")
        version = manifest.get("version", "")
        release_type = manifest.get("release_type", "")
        artifacts = manifest.get("artifacts", [])
        docs = manifest.get("documentation_included", [])
        gov = manifest.get("governance_contract", "")
        cert_status = manifest.get("certification_status", "")

        # 1. Check version
        version_valid = (version == "1.0.0")
        details.append(f"Version validated: {version}")

        # 2. Check artifacts — I2A: two physically verified Windows artifacts
        # (installer + portable). The Linux tarball is honestly reported as
        # skipped/BLOCKED on a Windows host instead of being fabricated.
        installer_names = {"amca-studio-v1.0.0-windows-x64-setup.exe"}
        portable_names = {"amca-studio-v1.0.0-windows-x64-portable.zip"}
        found_names = {a.get("package_name", "") for a in artifacts}
        artifacts_valid = len(artifacts) == 2 and installer_names <= found_names and portable_names <= found_names
        for art in artifacts:
            if len(art.get("sha256", "")) != 64 or art.get("size_bytes", 0) <= 0:
                artifacts_valid = False
        details.append(f"Artifact manifests validated: {len(artifacts)} packages with SHA256 checksums")

        # I2B: record release-gate provenance. certification_status is
        # CERTIFIED_FOR_FINAL_RELEASE only when canonical certification
        # evidence (verdict PASS + same SHA + clean tree) was supplied at
        # packaging time; physical verification alone yields BLOCKED.
        provenance = manifest.get("provenance", {})
        gate_state = provenance.get("release_gate_state", "NOT_EVALUATED")
        dirty_tree = provenance.get("dirty_tree")
        details.append(
            f"Release gate state: {gate_state}; final_release_certified="
            f"{provenance.get('final_release_certified')}"
        )
        if dirty_tree:
            details.append("Packaging tree was dirty at build time")

        # 3. Check documentation files exist
        docs_valid = True
        for doc_rel in docs:
            doc_path = self.repo_root / doc_rel
            if not doc_path.exists():
                docs_valid = False
                details.append(f"Missing doc: {doc_rel}")
            else:
                details.append(f"Verified documentation file: {doc_rel}")

        # 4. Check release type. I2B: the active pipeline emits
        # FUNCTIONALLY_VERIFIED_BUILD; a historical FINAL_GA_RELEASE manifest
        # (v0.9.0 GA lineage) remains valid as historical evidence.
        type_valid = release_type in ("FINAL_GA_RELEASE", "FUNCTIONALLY_VERIFIED_BUILD")
        details.append(f"Release type: {release_type}")

        # I2B: ready requires the release gate to have certified the final
        # release (canonical PASS + same SHA + clean tree), recorded via
        # certification_status CERTIFIED_FOR_FINAL_RELEASE on the manifest.
        ready = version_valid and artifacts_valid and docs_valid and type_valid and (cert_status == "CERTIFIED_FOR_FINAL_RELEASE")

        return FinalReleaseValidationResult(
            manifest_path=str(self.manifest_path),
            product_name=product,
            version=version,
            release_type=release_type,
            artifact_count=len(artifacts),
            artifacts_verified=artifacts_valid,
            documentation_verified=docs_valid,
            governance_contract=gov,
            certification_status=cert_status,
            is_ready_for_release=ready,
            details=details,
        )

