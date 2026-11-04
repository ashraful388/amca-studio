# Project lifecycle CLI

AMCA keeps project creation, execution recovery, and user stop as separate
operations. All commands below use `.amca/amca.sqlite3` unless `--database` is
provided.

## Create an explicit Goal Contract

```powershell
python -m amca project create `
  --project-id research-001 `
  --goal "Produce and verify a numerical result" `
  --criterion-json '{"id":"answer","description":"The answer equals 42.","evaluator_kind":"exact_value","expected_value":42}' `
  --constraint "Do not transmit project data." `
  --max-iterations 6 `
  --max-runtime-seconds 300 `
  --max-api-cost 0 `
  --privacy offline
```

For repeatable setup, pass `--contract contract.json`. The fresh-contract file
accepts `project_id`, `goal` (or `objective`), `criteria` (or
`success_criteria`), `constraints`, `budget`, `privacy_mode`, and
`compute_policy`:

```json
{
  "project_id": "research-001",
  "objective": "Produce and verify a numerical result",
  "success_criteria": [
    {
      "criterion_id": "answer",
      "description": "The answer equals 42.",
      "required": true,
      "evaluator_kind": "exact_value",
      "expected_value": 42,
      "evidence_requirement": 1,
      "decision_class": "significant"
    }
  ],
  "constraints": ["Do not transmit project data."],
  "budget": {
    "max_iterations": 6,
    "max_runtime_seconds": 300,
    "max_api_cost_usd": 0
  },
  "privacy_mode": "offline",
  "compute_policy": "balanced"
}
```

Creation is fail-closed: project identifiers must be safe path components,
criterion identifiers must be unique, at least one criterion must be required,
budgets must be finite, credential-like fields are rejected, and an existing
project is never overwritten. Runtime result fields such as `status`,
`satisfied`, evidence, and decision IDs are not accepted in a fresh contract.

## List or inspect

```powershell
python -m amca project list
python -m amca project list --status stopped
python -m amca project show --project-id research-001
python -m amca project show --project-id research-001 --version 0
```

`list` prints bounded summaries rather than transcript or artifact content.
`show --version` reads an immutable historical snapshot.

## Stop and recover

```powershell
python -m amca project stop `
  --project-id research-001 `
  --reason "Stopped by the operator"

python -m amca project resume --project-id research-001 --dry-run
python -m amca project resume --project-id research-001
```

Stop records a versioned lifecycle event and does not claim the Goal Contract
succeeded. Repeating stop is idempotent. Resume only replays already-durable
pending semantic events; it does not restart model calls or external actions.
