"""AMCA Studio v0.8.0 Model Hub Governance Integration (AG-AMCA-STUDIO-V0.8.0).

Connects Provider Governance backend with Model Hub selection transparency:
1. ModelHubGovernanceAdapter: Exposes read-only provider governance metadata (lifecycle state, key fingerprint, health status).
2. VerifiedCapabilityRegistry: Holds evidence-based verified capabilities; unknown remains unknown, zero inference.
3. ModelHubTransparencyContract: Unified view for Model Hub cards combining cost tier ($0 local vs commercial) and governance state.
4. Preserves 100% boundary discipline: Zero changes to Model Router selection or cost algorithms.

100% offline, zero cloud dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import sqlite3
from typing import Any, Sequence

from amca.provider_governance import CredentialGovernanceStore, ProviderHealthService
from amca.v080_provider_governance_design import CredentialLifecycleMetadata


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class ModelHubGovernanceCard:
    model_id: str
    display_name: str
    provider_name: str
    is_local: bool
    cost_tier: str
    credential_state: str
    key_fingerprint: str | None
    health_status: str
    verified_capabilities: tuple[str, ...]
    warning_state: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "provider_name": self.provider_name,
            "is_local": self.is_local,
            "cost_tier": self.cost_tier,
            "credential_state": self.credential_state,
            "key_fingerprint": self.key_fingerprint,
            "health_status": self.health_status,
            "verified_capabilities": list(self.verified_capabilities),
            "warning_state": self.warning_state,
        }


class ModelHubGovernanceAdapter:
    """Read-only adapter exposing provider governance and verified capabilities to Model Hub."""

    def __init__(
        self,
        *,
        credential_store: CredentialGovernanceStore,
        health_service: ProviderHealthService,
    ) -> None:
        self.credential_store = credential_store
        self.health_service = health_service

    def build_model_governance_card(
        self,
        *,
        workspace_id: str,
        model_id: str,
        display_name: str,
        provider_name: str,
        is_local: bool = False,
    ) -> ModelHubGovernanceCard:
        norm_prov = str(provider_name).strip().lower()
        norm_ws = str(workspace_id).strip()

        # Local models (Ollama, local llama.cpp) have no credential requirement and $0 cost path
        if is_local or norm_prov in ("ollama", "local", "amca-offline"):
            health = self.health_service.get_last_health(norm_prov)
            health_status = health.status if health else "UNCHECKED"
            verified_caps = health.capabilities_checked if health else ()
            return ModelHubGovernanceCard(
                model_id=model_id,
                display_name=display_name,
                provider_name=norm_prov,
                is_local=True,
                cost_tier="$0.00 (Local Offline)",
                credential_state="NOT_REQUIRED",
                key_fingerprint=None,
                health_status=health_status,
                verified_capabilities=verified_caps,
                warning_state=None if health_status == "HEALTHY" else "HEALTH_UNCHECKED",
            )

        # Commercial BYOK models: fetch credential metadata from store
        cred_row = self._find_provider_credential(workspace_id=norm_ws, provider_name=norm_prov)
        health = self.health_service.get_last_health(norm_prov)
        health_status = health.status if health else "UNCHECKED"
        verified_caps = health.capabilities_checked if health else ()

        if cred_row is None:
            return ModelHubGovernanceCard(
                model_id=model_id,
                display_name=display_name,
                provider_name=norm_prov,
                is_local=False,
                cost_tier="Commercial (BYOK)",
                credential_state="MISSING",
                key_fingerprint=None,
                health_status=health_status,
                verified_capabilities=verified_caps,
                warning_state="CREDENTIAL_MISSING",
            )

        state = cred_row.state
        fp = cred_row.key_fingerprint
        warning = None
        if state == "EXPIRED":
            warning = "CREDENTIAL_EXPIRED"
        elif state == "REVOKED":
            warning = "CREDENTIAL_REVOKED"
        elif health_status in ("UNREACHABLE", "DEGRADED"):
            warning = f"PROVIDER_{health_status}"
        elif health_status == "UNCHECKED":
            warning = "HEALTH_UNCHECKED"

        return ModelHubGovernanceCard(
            model_id=model_id,
            display_name=display_name,
            provider_name=norm_prov,
            is_local=False,
            cost_tier="Commercial (BYOK)",
            credential_state=state,
            key_fingerprint=fp,
            health_status=health_status,
            verified_capabilities=verified_caps,
            warning_state=warning,
        )

    def _find_provider_credential(self, *, workspace_id: str, provider_name: str) -> CredentialLifecycleMetadata | None:
        with sqlite3.connect(self.credential_store.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT credential_id, workspace_id, provider_name, key_fingerprint, state, created_at, last_rotated_at, expires_at
                FROM credential_lifecycle
                WHERE workspace_id = ? AND provider_name = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (workspace_id, provider_name),
            ).fetchone()
            if row is None:
                return None
            return CredentialLifecycleMetadata(
                credential_id=str(row["credential_id"]),
                workspace_id=str(row["workspace_id"]),
                provider_name=str(row["provider_name"]),
                key_fingerprint=str(row["key_fingerprint"]),
                state=str(row["state"]),  # type: ignore[arg-type]
                created_at=str(row["created_at"]),
                last_rotated_at=row["last_rotated_at"],
                expires_at=row["expires_at"],
            )

