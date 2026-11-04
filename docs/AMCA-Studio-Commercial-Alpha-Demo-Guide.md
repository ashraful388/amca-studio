# AMCA Studio: Commercial Alpha User & Demo Guide

**Release Version**: AMCA Studio v0.6.0 Alpha  
**Audience**: Alpha Testers, Enterprise Evaluators, Solutions Engineers  
**Operating Mode**: Local-First Hybrid Cognition & Autonomous Multi-Agent Workflows  

---

## 1. Welcome to AMCA Studio

AMCA Studio is a commercial-grade, local-first intelligence platform combining adaptive multi-agent cognition, persistent memory, version-controlled artifacts, and durable automation pipelines. Unlike standard chat wrappers, AMCA Studio orchestrates collaborative specialized Nano Agents governed by deterministic safety boundaries and cost controls.

---

## 2. Fast-Track First-Run Experience (5-Minute Quickstart)

### Step 1: Initialize Workspace Project
1. Open the AMCA Studio Desktop Application (`http://localhost:1420` or desktop executable).
2. On the **Projects Overview** dashboard, click **`+ New Project`**.
3. Name your project (e.g., `Enterprise Compliance Audit`) and select the **Software Engineering** or **Research Synthesis** template.
4. The system initializes a scoped SQLite partition, memory graph, and artifact knowledge registry.

### Step 2: Configure Model Hub (BYOK or Local Models)
1. Navigate to **Models** in the primary sidebar.
2. AMCA Studio automatically scans and lists available local models (Ollama / LocalAI).
3. Under **Cloud Providers**, enter your preferred API key (OpenAI, Anthropic, Google Gemini, DeepSeek, or Groq).
4. *Security Guarantee*: Keys are encrypted in local OS-keychain storage and never transmitted to third-party telemetry servers.

### Step 3: Run First Multi-Agent Task in Task Composer
1. Click **`New Chat`** in your project sidebar.
2. Select an execution collaboration mode:
   - **Solo Mode**: Single model fast response.
   - **Joint Synthesis**: Collaborative draft and peer review.
   - **Parallel Fanout**: High-throughput multi-perspective analysis.
   - **Adaptive Hybrid**: Dynamic model routing and budget optimization.
3. Submit an objective prompt:
   > *"Perform a security audit of our backend authentication contracts and document all findings in a structured Markdown artifact."*
4. Watch the real-time agent lifecycle and reasoning steps in the collaboration timeline.

### Step 4: Inspect Artifacts & Persistent Knowledge
1. Navigate to **Artifacts** in the sidebar.
2. Review the newly generated audit report in the **Bounded Preview** tab.
3. Switch to **Version Lineage** to review SHA-256 integrity digests and version progression.
4. Click **`+ Link Memory`** to pin critical takeaways to project memory for future sessions.

### Step 5: Automate with Guided Workflow Center
1. Navigate to **Automations** under Advanced Navigation.
2. Click **`+ New Automation`** or choose the **Automated Code & PR Review** starter template.
3. Set schedule type (`once` or `recurring` cron syntax like `0 9 * * 1-5`).
4. Enter target branch parameters (e.g., `{target_branch}`) and click **Run**.
5. Inspect deterministic occurrence logs and execution history.

---

## 3. Recommended Alpha Demo Workflows

### Demo Scenario A: Autonomous Pull Request & Security Review
- **Goal**: End-to-end automated code auditing and artifact generation.
- **Workflow**:
  1. Create automation with template `Automated Code & PR Review`.
  2. Set goal: `Review git commits on {branch} for SQL injection risks and authentication flaws.`
  3. Trigger execution with parameter `branch: staging`.
  4. Verify generated review report in the Artifact Knowledge Workspace.

### Demo Scenario B: Research Literature & Evidence Digest
- **Goal**: Multi-step evidence synthesis with persistent claim anchors.
- **Workflow**:
  1. Open Research Center and submit a literature query.
  2. Synthesize findings across multiple papers.
  3. Export structured summaries to PDF and Markdown in the Export Center.
  4. Anchor core empirical conclusions into persistent project memory.

---

## 4. Error Diagnostics & Recovery Actions

| Failure Scenario | Cause | Recommended Action |
| :--- | :--- | :--- |
| **Provider Timeout / 429** | Cloud API rate limit exceeded | Open Model Hub, switch default provider or enable local model fallback. |
| **Interrupted Run** | Application restarted during active execution | System marks state as `interrupted` (zero false completion); click **Continue Run** to resume from last checkpoint. |
| **Version Conflict (409)** | Concurrent edits to historical artifact | Reload latest artifact state; non-destructive restore appends as new head `vN+1`. |
| **CORS / Unauthorized (401/403)** | Missing session token or foreign origin | Refresh desktop window to negotiate valid local session header. |

---

## 5. Known Alpha Boundaries

- **Single Host Scope**: Designed for local workstation execution (`127.0.0.1`); remote team multi-tenancy scheduled for v0.7.
- **Storage Limits**: SQLite database handles up to 100,000 artifacts and memory nodes locally with sub-second performance.
- **Diff Computation**: Unstored inline token diffs display full version snapshots with timestamp and creator attribution.

