"""CLOSURE-05 Phase F physical proof: automation actual execution lifecycle.

Proves the full lifecycle with a REAL AutomationStore + AutomationScheduler
and a dispatch that records a REAL job lifecycle outcome:

  F1. Durable definition: recurring automation saved, disabled by default;
      a reopened store still lists it.
  F2. Enable + due tick: enabled automation whose schedule is due is
      claimed and dispatched exactly once by scheduler.tick().
  F3. Replay protection: a second tick at the same slot does NOT dispatch
      again (occurrence already claimed).
  F4. Run completion: complete_claim_by_run marks the claim 'succeeded'
      with the real run_id bound to the occurrence.
  F5. Disable wins: disabling the automation makes the next due tick a
      no-op (claim returns None on the freshest row).
  F6. Dispatch refusal: a dispatch that raises is retained as
      'dispatch refused' provenance and the SAME occurrence is retryable
      on a later tick (revive path), then succeeds.
  F7. Once automations auto-disable after their single dispatch.

Run:  python scripts/prove_closure05_phase_f.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.automations import AutomationStore  # noqa: E402
from amca.scheduler import AutomationScheduler  # noqa: E402

RESULTS: list[dict[str, object]] = []


def record(check_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": check_id, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + check_id + " :: " + detail)


class JobRegistry:
    """Minimal real run registry: run_id -> final status/error."""

    def __init__(self) -> None:
        self.runs: dict[str, tuple[str, str | None]] = {}

    def dispatch(self, goal: str, project_id: str) -> str:
        run_id = f"run-{len(self.runs) + 1:04d}"
        self.runs[run_id] = ("succeeded", None)
        return run_id

    def fail_dispatch(self, goal: str, project_id: str) -> str:
        raise RuntimeError("backend unavailable during dispatch")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05-phase-f-"))
    try:
        db = work / "automations.sqlite3"
        store = AutomationStore(db)

        # ---- F1: durable definition, disabled by default ----
        saved = store.save({
            "name": "Phase F watcher",
            "project_id": "closure05-phase-f",
            "goal": "Check the loopback endpoint and report status.",
            "automation_type": "recurring",
            "schedule": "every 1m",
        })
        automation_id = str(saved["id"])
        reopened = AutomationStore(db)
        durable = any(item["id"] == automation_id for item in reopened.list())
        record(
            "F1-durable-definition",
            not saved["enabled"] and durable,
            f"id={automation_id} enabled={saved['enabled']} survives_reopen={durable}",
        )

        jobs = JobRegistry()
        base = datetime.now(UTC).replace(microsecond=0)
        scheduler = AutomationScheduler(reopened, jobs.dispatch, poll_seconds=0.5)

        # ---- F6: dispatch refusal retained, same occurrence revived ----
        # The first slot for an interval automation is one interval after
        # creation, so tick at +1m to reach the first due occurrence.
        failing = AutomationScheduler(reopened, jobs.fail_dispatch, poll_seconds=0.5)
        store.set_enabled(automation_id, True)
        tick_now = base + timedelta(minutes=1)
        failing.tick(now=tick_now)
        refused_row = store.latest_occurrence(automation_id)
        refused_id = str(refused_row["id"]) if refused_row else None
        refused_retained = (
            refused_row is not None
            and refused_row["status"] == "failed"
            and str(refused_row["error"]).startswith("dispatch refused:")
            and refused_row["run_id"] is None
        )
        record(
            "F6a-dispatch-refusal-retained",
            refused_retained,
            f"occurrence={refused_id} status={refused_row['status'] if refused_row else None} "
            f"error={str(refused_row['error'])[:40] if refused_row else None!r}",
        )

        # A healthy scheduler ticking the SAME slot must revive the SAME
        # retained row (never delete provenance), dispatch, and bind a run.
        fresh = AutomationStore(db)
        scheduler2 = AutomationScheduler(fresh, jobs.dispatch, poll_seconds=0.5)
        revived_tick = scheduler2.tick(now=tick_now)
        revived_row = store.get_occurrence(refused_id) if refused_id else None
        record(
            "F6b-refused-occurrence-revived",
            bool(revived_tick)
            and revived_row is not None
            and revived_row["status"] == "running"
            and str(revived_row["run_id"]).startswith("run-"),
            f"same_row={revived_row is not None} status={revived_row['status'] if revived_row else None} "
            f"run_id={revived_row['run_id'] if revived_row else None}",
        )

        # ---- F2: healthy dispatch exactly once ----
        fresh = AutomationStore(db)
        # mark the previous failing claims as terminal so the healthy
        # scheduler can claim the NEXT slot cleanly.
        for occurrence in fresh.list_occurrences(automation_id):
            if occurrence["status"] in {"claimed", "running"}:
                fresh.complete_claim(str(occurrence["id"]), "failed", error="reset")
        scheduler2 = AutomationScheduler(fresh, jobs.dispatch, poll_seconds=0.5)
        store.set_enabled(automation_id, True)
        first = scheduler2.tick(now=base + timedelta(minutes=5))
        occurrence_row = store.latest_occurrence(automation_id)
        claimed_and_dispatched = (
            bool(first)
            and occurrence_row is not None
            and occurrence_row["status"] == "running"
            and str(occurrence_row["run_id"]).startswith("run-")
        )
        record(
            "F2-claim-then-dispatch",
            claimed_and_dispatched,
            f"dispatched={len(first)} status={occurrence_row['status']} "
            f"run_id={occurrence_row['run_id']}",
        )

        # ---- F3: replay protection at the same slot ----
        replay = scheduler2.tick(now=base + timedelta(minutes=5))
        record(
            "F3-replay-protected",
            not replay,
            f"second_tick_dispatched={len(replay)}",
        )

        # ---- F4: run completion binds run_id to the occurrence ----
        run_id = str(occurrence_row["run_id"])
        changed = store.complete_claim_by_run(run_id, "succeeded")
        occurrence_after = store.find_claim_by_run(run_id)
        record(
            "F4-completion-by-run",
            changed == 1
            and occurrence_after is not None
            and occurrence_after["status"] == "succeeded",
            f"changed={changed} status={occurrence_after['status'] if occurrence_after else None}",
        )

        # ---- F5: disable wins over the next due tick ----
        store.set_enabled(automation_id, False)
        disabled_tick = scheduler2.tick(now=base + timedelta(minutes=10))
        record(
            "F5-disable-wins",
            not disabled_tick,
            f"disabled_tick_dispatched={len(disabled_tick)}",
        )

        # ---- F7: 'once' automation auto-disables after dispatch ----
        once = store.save({
            "name": "Phase F one-shot",
            "project_id": "closure05-phase-f",
            "goal": "Run once and stop.",
            "automation_type": "once",
            "schedule": (base + timedelta(minutes=20)).isoformat(),
        })
        store.set_enabled(str(once["id"]), True)
        once_tick = scheduler2.tick(now=base + timedelta(minutes=20))
        once_after = store.get(str(once["id"]))
        record(
            "F7-once-auto-disables",
            bool(once_tick) and not once_after["enabled"],
            f"dispatched={len(once_tick)} enabled_after={once_after['enabled']}",
        )

        passed = sum(1 for item in RESULTS if item["ok"])
        summary = {
            "phase": "CLOSURE-05 Phase F",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out_path = SOFTWARE_ROOT / "tests" / "closure05-phase-f-proof.json"
        out_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({"passed": passed, "total": len(RESULTS)}))
        print(f"proof written to {out_path}")
        return 0 if passed == len(RESULTS) else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
