# AMCA Studio v0.6.0 Commercial Alpha Release Notes

**Release**: AMCA Studio v0.6.0 Alpha RC1  
**Distribution Channel**: Controlled Commercial Alpha  
**Platforms**: Windows 10/11 x64 (NSIS Installer, MSI, Portable ZIP)  
**Architecture**: Local-First Hybrid Cognition & Autonomous Agent Fabric  

---

## 1. What is AMCA Studio?

AMCA Studio is a commercial-grade, local-first intelligence platform that empowers technical teams to orchestrate specialized AI agents, manage persistent knowledge graphs, author version-controlled artifacts, and execute scheduled automation pipelines on their own machines.

---

## 2. Core Capabilities & Architectural Pillars

### 🎯 Workspace & Projects Dashboard
- Multi-project scoping with clean template presets (Software Engineering, Research Synthesis).
- Instant project switching and strict workspace boundary isolation.

### 🤖 Agent Fabric & Task Composer
- Multi-model orchestration supporting **Solo**, **Joint Synthesis**, **Parallel Fanout**, and **Adaptive Hybrid** modes.
- Real-time collaboration timeline with deterministic round descriptions and execution traces.
- Nano Agent specialization hierarchy with cost tracking and budget gates.

### 🧠 Persistent Memory Graph
- Single canonical SQLite store (`amca.sqlite3`) for project directives, insights, and facts.
- Explicit memory pinning and 1-click association with project artifacts.

### 📑 Artifact Intelligence Workspace
- First-class support for Markdown, Spreadsheets, Presentations, and Code artifacts.
- Immutable version history with SHA-256 integrity digests and non-destructive restore (`vN+1`).
- Multi-format Export Center (Markdown, PDF, HTML, JSON, CSV) with historical export logs.

### ⚙️ Automation Center
- Durable task scheduling (`once` or recurring cron schedules e.g. `0 9 * * 1-5`).
- Safe parameter interpolation (e.g. `{target_branch}`) and 1-click workflow cloning.

### 🔒 Enterprise Security & Privacy
- Zero cloud dependency for local reasoning; Bring-Your-Own-Key (BYOK) stored in local OS keychains.
- Strict Content Security Policy (`default-src 'self'`), loopback binding (`127.0.0.1`), and desktop session token auth.

---

## 3. Installation & Getting Started

### Standard Installer (Recommended)
1. Run `AMCA-Studio-Setup-v0.6.0.exe`.
2. Follow the NSIS installation wizard. The app will install to `%LocalAppData%\Programs\AMCA` and place a shortcut in Start Menu.
3. Launch AMCA Studio and configure your local Ollama engine or Cloud API keys in **Model Hub**.

### Portable Edition
1. Extract `AMCA-Studio-Portable-v0.6.0.zip` to any folder.
2. Run `AMCA.exe`. All user data and SQLite databases will reside in `%AppData%\AMCA`.

---

## 4. Known Boundaries & Limitations for Alpha

- **Single Workstation Scope**: Multi-user cloud synchronization is reserved for v0.7.
- **Model Compatibility**: Requires Ollama `v0.1.20+` for local models, or standard OpenAI / Anthropic / Gemini API keys for cloud models.
- **Large Payloads**: Individual artifact versions are bounded to 5 MB per revision to ensure fast deterministic SQLite transactions.

