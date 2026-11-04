# AMCA Studio: v0.8.0 Provider Health & Credential Governance Design

**Milestone**: `AG-AMCA-STUDIO-V0.8.0-PROVIDER-HEALTH-AND-CREDENTIAL-GOVERNANCE-DESIGN-01`  
**Product**: AMCA Studio (v0.8.0 Architecture Track)  
**Status**: **PROVIDER GOVERNANCE DESIGN APPROVED (PROVIDER_GOVERNANCE_DESIGN_APPROVED)**  
**Target Version**: `v0.8.0`  
**Date**: September 2, 2026  

---

## 1. Executive Summary

This architecture milestone designs the governance, lifecycle, and health verification mechanisms for local and remote AI providers:
1. **Credential Lifecycle State Machine**:
   - Explicit states: `ACTIVE`, `ROTATING`, `ROTATED`, `REVOKED`, `EXPIRED`.
   - In-place atomic key rotation without destructive delete/re-add operations.
2. **Zero-Plaintext Security & Audit Invariant**:
   - Sensitive keys and tokens are never stored in plaintext or recorded in logs/audit events.
   - Audits capture cryptographic key fingerprints (`key_fingerprint`), operator role, provider name, and validation outcome.
3. **Four-Stage Atomic Rotation Protocol**:
   - `PRE_ROTATION_VALIDATE`: Pre-flight capability handshake with the proposed new credential.
   - `ATOMIC_SWAP`: Staging the new encrypted credential in an isolated transaction.
   - `POST_ROTATION_VERIFY`: Verifying end-to-end provider execution before final commitment.
   - `COMMIT_AND_AUDIT`: Committing state to `ACTIVE`, archiving the previous credential as `ROTATED`, appending immutable audit log. Rollback available at every stage prior to commit.
4. **Explicit Provider Health Model**:
   - Records `provider_name`, `endpoint_url`, `last_checked`, `status` (`HEALTHY`, `DEGRADED`, `UNREACHABLE`, `UNCHECKED`), `failure_reason`, and `capabilities_checked`.
   - Pure on-demand, explicit check model—strictly prohibits background polling daemons or telemetry monitoring.

---

## 2. Rotation & Health Specification Matrix

| Component | Invariant | Governance Mechanism | Security Assurance |
| :--- | :--- | :--- | :--- |
| **Credential Storage** | Zero plaintext | Local platform keyring / encrypted envelope | Key never leaks to logs or UI |
| **Rotation Workflow** | 4-Stage Atomic Swap | Pre-validate -> Swap -> Verify -> Commit | Zero downtime, instant rollback on failure |
| **Audit Ledger** | Fingerprint logging | `CredentialRotationAuditEvent` | Proof of rotation without secret leakage |
| **Health Verification** | On-demand only | Explicit capability handshake | Zero continuous background polling / telemetry |

---

## 3. Test & Verification Summary

| Test Area | Tested Invariant | Result |
| :--- | :--- | :--- |
| **Lifecycle States** | ACTIVE, ROTATING, ROTATED, REVOKED, EXPIRED verified | **PASS** |
| **Secret Protection** | Metadata and audit records use key fingerprints only | **PASS** |
| **Rotation Workflow** | 4 sequential stages with explicit rollback documented | **PASS** |
| **Health Contract** | Complete schema and valid statuses verified | **PASS** |
| **Anti-Telemetry Guard** | Background monitoring strictly forbidden | **PASS** |
| **Studio Regression** | Full studio regression suite passes across all 219 tests | **PASS** |

