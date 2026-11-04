# AMCA Studio: Alpha Operational Runbook

**Milestone**: `AG-AMCA-STUDIO-P3-ALPHA-SUPPORT-AND-OPERATIONS-READINESS-01`  
**Purpose**: Standard Operating Procedures (SOP) for Alpha Operations, Incident Triage, and Support Escalation.  
**Date**: September 2, 2026  

---

## 1. Startup & Health Verification SOP

1. **Service Binding**: Confirm service binds to `127.0.0.1:8765`.
2. **Health Ping**: Send `GET http://127.0.0.1:8765/api/health` with `Origin: http://localhost:1420`.
   - Expected: `{"status": "ok", "protocol_version": 15}`.
3. **Session Handshake**: Send `GET /api/session` to obtain an ephemeral session token.

---

## 2. Safe Diagnostics Collection & Privacy Rules

When requesting user diagnostic logs:
- **Never collect raw database files**: `amca.sqlite3` contains user project instructions and confidential code snippets.
- **Allowed Artifacts**:
  - `background-lifecycle.jsonl`: Contains only process start/stop timestamps, port assignments, and error status codes.
  - Sanitized runtime output: Stripped of bearer tokens and keys.
- **Support Escalation Tiering**:
  - **Tier 1 (Community / Discord)**: Configuration issues, local model sizing recommendations.
  - **Tier 2 (Core Engineering)**: Native crashing bugs, reproducible SQLite locking errors.

