"""A2.1D real Windows kill/restart validation (zero-cost fake adapters).

Modes:
  phase1  Run a real NanoScheduler organization in THIS process: task-fast
          commits and settles, task-slow crosses the dispatch boundary into
          a long provider sleep. The parent hard-kills this process there.
  verify  New process: reopen the SAME canonical database, run startup
          reconciliation, build the continuation plan, and assert final
          truth (fast=REUSE, slow=blocked UNCERTAIN, zero redispatch).

Parent orchestration lives in tests/test_agent_composite_recovery.py
(test_real_windows_kill_restart).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.agent_continuation import (  # noqa: E402
    ACTION_BLOCK_UNCERTAIN,
    ACTION_REUSE,
    build_continuation_plan,
)
from amca.agent_recovery import AgentRecoveryStore  # noqa: E402
from amca.agent_reconciliation import reconcile_agent_fabric_state  # noqa: E402
from amca.agent_runtime import ExecutorKind, NanoScheduler  # noqa: E402


class FastAdapter:
    is_local = True
    is_conservative_bound = True
    cost_usd = 0.01

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        usage = type("Usage", (), {"cost_usd": self.cost_usd})()
        return type("Response", (), {"text": "fast result", "usage": usage})()


class SlowAdapter(FastAdapter):
    async def generate(self, request):
        self.calls += 1
        Path(sys.argv[3]).write_text("slow-dispatched", encoding="utf-8")
        time.sleep(300)


def phase1(database: str, marker: str) -> None:
    store = AgentRecoveryStore(Path(database))
    fast, slow = FastAdapter(), SlowAdapter()
    scheduler = NanoScheduler(
        {"fast": fast, "slow": slow}, budget_usd=10.0,
        max_concurrent=1, recovery_store=store,
    )
    org = scheduler.schedule(
        [
            {"id": "task-fast", "agent_id": "fast",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "fast"},
            {"id": "task-slow", "agent_id": "slow",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "slow"},
        ],
        organization_id="org-restart", project_id="p", goal="kill test",
    )
    asyncio.run(scheduler.run(org))
    Path(marker).write_text("finished", encoding="utf-8")


def verify(database: str) -> None:
    store = AgentRecoveryStore(Path(database))
    result = reconcile_agent_fabric_state(store)
    plan_tasks = {
        task_id: entry["action"]
        for task_id, entry in build_continuation_plan(
            store, "org-restart"
        )["tasks"].items()
    }
    report = {
        "integrity_errors": result["summary"]["integrity_errors"],
        "task_fast": plan_tasks.get("task-fast"),
        "task_slow": plan_tasks.get("task-slow"),
        "expected": {"task_fast": ACTION_REUSE, "task_slow": ACTION_BLOCK_UNCERTAIN},
    }
    ok = (
        report["integrity_errors"] == 0
        and report["task_fast"] == ACTION_REUSE
        and report["task_slow"] == ACTION_BLOCK_UNCERTAIN
    )
    print("RESTART_VALIDATION_" + ("PASS" if ok else "FAIL"))
    print(json.dumps(report))


if __name__ == "__main__":
    mode, database = sys.argv[1], sys.argv[2]
    if mode == "phase1":
        phase1(database, sys.argv[3])
    else:
        verify(database)
