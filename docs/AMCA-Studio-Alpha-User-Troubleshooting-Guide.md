# AMCA Studio: Alpha User Troubleshooting Guide

**Milestone**: `AG-AMCA-STUDIO-P3-ALPHA-SUPPORT-AND-OPERATIONS-READINESS-01`  
**Target Audience**: Alpha Testers & Support Engineers  
**Date**: September 2, 2026  

---

## 1. Quick Diagnostic Flowchart

When encountering an unexpected behavior:
1. Check backend connectivity via top status indicator (Green = Connected, Amber = Reconnecting, Red = Disconnected).
2. Check local Ollama or Cloud API key settings in **Model Hub**.
3. View background events in the Activity Log tray.

---

## 2. Common Scenarios & Recovery Recipes

### Scenario A: Application Launches to Blank Window or "Connecting..." Spinner
- **Root Cause**: Backend sidecar (`amca-backend.exe` or local Python process) took longer than 5 seconds to bind to port 8765, or port 8765 is occupied.
- **Safe Diagnosis**: Open PowerShell and run: `Test-NetConnection -ComputerName 127.0.0.1 -Port 8765`.
- **Recovery Action**: If another program holds 8765, close the conflicting program and relaunch AMCA Studio.

### Scenario B: Model Not Responding / "Provider Not Configured"
- **Root Cause**: Local Ollama daemon is offline or Cloud API key was revoked.
- **Safe Diagnosis**: Run `ollama list` in terminal to confirm Ollama service is active.
- **Recovery Action**: Open **Model Hub**, verify the selected model has a green checkmark, and re-test connection.

### Scenario C: Interrupted Task Run or Abrupt Power Loss
- **Root Cause**: Machine suspended or app closed mid-execution.
- **Safe Diagnosis**: Check the project's task list. SQLite automatically rolls forward committed states upon reopen.
- **Recovery Action**: Re-trigger the task; prior versions and artifacts are safely preserved in version history.

