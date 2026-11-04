# Model Hub and model-access Agent Fabric

Implemented in the R3 desktop architecture:

- one Settings → Model Hub state and UI;
- three access sources: AMCA API, BYOK providers, and local models;
- provider-centric BYOK metadata with OS-vault credential isolation;
- versioned endpoint, pricing, Model Team, and task-cost storage;
- event-driven MAIA plus eight normally dormant Nano role contracts;
- deterministic local compatibility evaluation with green/red UI status;
- deterministic Cost-Quality Gate and task-time Auto Intelligence router wired
  into desktop Chat execution;
- authoritative Manual model-set routing;
- Auto Intelligence / Manual task-time UI without user-facing collaboration-mode controls;
- post-run task-cost finalization with AMCA API, BYOK, and local fields;
- semantic events for provisioning, provider, catalog, pricing, local install, Model Team, selection, and task-cost changes.

Commercial boundaries remain intentionally inactive: AMCA account provisioning,
credit purchase/subscription, the managed gateway, AWS Bedrock billing, and live
provider discovery require their deterministic server infrastructure. The desktop
UI reports these as unavailable instead of fabricating credentials, catalogs,
prices, or charges. Local model installation remains an explicit runtime/system
operation; AMCA does not silently download third-party weights.

Auto Intelligence exposes no model picker. It analyzes the task and attachments,
builds the eligible Local/BYOK/managed candidate set, applies capability, quality,
privacy, context, reliability, hardware, and expected-cost gates, then selects the
smallest adequate participant set. Manual remains the only mode in which the user
selects logical models or a saved Model Team.
