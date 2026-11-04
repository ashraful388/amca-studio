"""B1 true automations scheduler.

Turns durable automation definitions into real background execution: a
polling daemon computes due automations from their schedule grammar,
commits a durable per-occurrence claim BEFORE dispatching through an
injected callback (the existing permissioned run path), records
last_run/last_run_id, disables one-shot automations after firing, and
applies an explicit missed-run policy: work that came due while the
scheduler was stopped runs exactly once on restart and is flagged
``missed`` in its metadata so nothing fires silently twice.

Since B1.2A the claim is keyed by a canonical occurrence slot derived
only from durable state (never from the dispatch wall-clock), so the
same logical occurrence computes the same identity across restarts and
a crash between claiming and running can never duplicate paid work.
Scheduler bookkeeping only ever updates scheduler-owned columns, so it
cannot clobber a user edit that lands after the scheduler's snapshot.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .conditions import ConditionResult

INTERVAL_PATTERN = re.compile(r"^every\s+(\d+)([smh])$")
DAILY_PATTERN = re.compile(r"^daily\s+(\d{2}:\d{2})$")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _naive_utc(now: datetime) -> datetime:
    if now.tzinfo is not None:
        return now.astimezone(UTC).replace(tzinfo=None)
    return now


def next_run_at(
    schedule: str,
    automation_type: str,
    last_run_at: str | None,
    now: datetime,
) -> datetime | None:
    """Compute when this schedule should fire next (naive UTC), or None."""

    text = schedule.strip().casefold()
    if text == "manual":
        return None
    reference = _parse_iso(last_run_at)
    current = _naive_utc(now)

    interval = INTERVAL_PATTERN.match(text)
    if interval:
        amount = int(interval.group(1))
        unit = {"s": 1, "m": 60, "h": 3600}[interval.group(2)]
        delta = timedelta(seconds=amount * unit)
        if reference is None:
            # Never ran while enabled: due immediately.
            return current
        candidate = reference + delta
        # Catch-up cap: a long outage fires once now, not N times.
        return candidate if candidate > current else current

    daily = DAILY_PATTERN.match(text)
    if daily:
        hour, minute = (int(part) for part in daily.group(1).split(":"))
        today_target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        fired_today = reference is not None and reference >= today_target
        if current < today_target and not fired_today:
            return today_target
        if not fired_today and reference is not None:
            # Missed today's slot while enabled: catch up once now.
            return today_target
        return today_target + timedelta(days=1)

    # An exact ISO datetime is a one-shot absolute slot for ANY automation
    # type (B1.2B §10: a once-scheduled conditional still needs its slot).
    try:
        return _parse_iso(text)
    except ValueError:
        pass
    raise ValueError(f"unsupported schedule grammar: {schedule!r}")


def occurrence_slot(
    schedule: str,
    automation_type: str,
    last_run_at: str | None,
    created_at: str | None,
    now: datetime,
) -> datetime | None:
    """Canonical identity of the occurrence that is due right now.

    This is a deterministic function of durable state only (last_run_at,
    created_at, schedule) plus calendar arithmetic — never of the dispatch
    wall-clock — so the same logical occurrence maps to the same claim key
    across process restarts. It mirrors next_run_at's firing decision but
    keeps the unclamped slot for overdue intervals so a crash between
    claiming and running blocks exactly this occurrence after restart.
    """

    text = schedule.strip().casefold()
    if text == "manual":
        return None
    reference = _parse_iso(last_run_at)
    current = _naive_utc(now)

    interval = INTERVAL_PATTERN.match(text)
    if interval:
        amount = int(interval.group(1))
        seconds = amount * {"s": 1, "m": 60, "h": 3600}[interval.group(2)]
        delta = timedelta(seconds=seconds)
        if reference is not None:
            return reference + delta
        anchor = _parse_iso(created_at) or current
        if current < anchor:
            return anchor
        elapsed = int((current - anchor).total_seconds())
        return anchor + timedelta(seconds=(elapsed // seconds) * seconds)

    daily = DAILY_PATTERN.match(text)
    if daily:
        hour, minute = (int(part) for part in daily.group(1).split(":"))
        today_target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        fired_today = reference is not None and reference >= today_target
        if current < today_target and not fired_today:
            return today_target
        if not fired_today and reference is not None:
            return today_target
        return today_target + timedelta(days=1)

    # Same ISO-once rule for the canonical slot computation.
    try:
        return _parse_iso(text)
    except ValueError:
        pass
    raise ValueError(f"unsupported schedule grammar: {schedule!r}")


_CONDITIONAL_FAMILY = frozenset({"conditional", "monitor", "event_triggered"})


def _is_once_schedule(schedule: str) -> bool:
    """True when the schedule is one absolute datetime (one-shot slot)."""

    text = schedule.strip().casefold()
    if not text or text == "manual":
        return False
    try:
        _parse_iso(text)
        return True
    except ValueError:
        return False


class AutomationScheduler:
    """Polling loop dispatching claimed, due automations through a run callback."""

    def __init__(
        self,
        store: Any,
        dispatch: Callable[[str, str], str],
        *,
        poll_seconds: float = 30.0,
        clock: Callable[[], datetime] = datetime.now,
        condition_evaluator_factory: Callable[[str], Any] | None = None,
        audit: Any | None = None,
        review_sink: Callable[[str, str, str, str, str], None] | None = None,
    ) -> None:
        # Optional B1.4A attention sink: review_sink(automation_id, project_id,
        # occurrence_id, kind, detail) fires on condition errors and dispatch
        # refusals so Studio's Review Center can surface them.
        self.store = store
        self.dispatch = dispatch
        self.poll_seconds = max(0.5, float(poll_seconds))
        self.clock = clock
        # Optional project_id -> ConditionEvaluator factory (B1.2B). When it
        # is absent, conditional automations fail closed without dispatching.
        self.condition_evaluator_factory = condition_evaluator_factory
        # Optional B1.3D lifecycle auditor (BackgroundAudit-compatible).
        self.audit = audit
        self.review_sink = review_sink
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # B1.3B observability: smallest truthful lifecycle state.
        self.started_at: str | None = None
        self.last_tick_at: str | None = None
        self.last_tick_error: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        if self.started_at is None:
            self.started_at = datetime.now(UTC).isoformat()
        if self.audit is not None:
            self.audit.record("scheduler_started")
        self._thread = threading.Thread(target=self._loop, name="amca-scheduler", daemon=True)
        self._thread.start()

    def _check_condition(
        self,
        definition: dict[str, Any],
        kind: str,
        claim: dict[str, Any],
        current: datetime,
    ) -> dict[str, Any] | None:
        """Run the deterministic condition phase for one claimed occurrence.

        Returns a terminal entry to record (skipped / error) when the
        occurrence ends without an action, or None when the condition is TRUE
        and the caller must continue into canonical dispatch.
        """

        automation_id = str(definition["id"])
        stamp = current.isoformat()
        entry: dict[str, Any] = {
            "automation_id": definition["id"],
            "run_id": None,
            "missed": False,
            "occurrence_id": claim["id"],
            "scheduled_for": claim["scheduled_for"],
        }

        def finish(result: str, status: str, reason: str) -> dict[str, Any]:
            self.store.complete_claim(str(claim["id"]), status, reason)
            # Once semantics apply to once-TYPED automations AND any
            # conditional scheduled on one absolute datetime slot (B1.2B
            # §10): exactly one check forever.
            if kind == "once" or _is_once_schedule(str(definition.get("schedule", ""))):
                self.store.set_enabled(automation_id, False)
            entry["condition_result"] = result
            return entry

        if kind != "conditional":
            reason = f"unsupported automation_type '{kind}' fails closed; no action executed"
            self.store.set_condition_check(
                str(claim["id"]),
                {"kind": kind, "checked_at": stamp, "result": "error", "error": reason},
            )
            self.store.record_check_at(automation_id, stamp)
            self.store.record_skip(automation_id, reason=reason)
            return finish("unsupported", "skipped", reason)

        factory = self.condition_evaluator_factory
        condition = definition.get("condition")
        if factory is None:
            reason = "no condition evaluator configured; failing closed"
        elif not isinstance(condition, Mapping):
            reason = "legacy conditional automation has no valid condition"
        else:
            reason = None
        if reason is not None:
            self.store.set_condition_check(
                str(claim["id"]),
                {"kind": "unknown", "checked_at": stamp, "result": "error", "error": reason},
            )
            self.store.record_check_at(automation_id, stamp)
            self.store.record_skip(automation_id, reason=reason)
            return finish("error", "failed", reason)

        evaluator = factory(str(definition.get("project_id", "")))
        try:
            result = evaluator.evaluate(condition)
        except Exception as error:  # defensive: evaluator must fail closed
            result = ConditionResult(
                kind=str(condition.get("kind")),
                ok=False,
                error=f"evaluator crashed: {error}",
            )
        payload: dict[str, Any] = {
            "kind": result.kind,
            "version": condition.get("version"),
            "checked_at": stamp,
            "result": ("true" if result.value else "false") if result.ok else "error",
            "evidence": result.evidence,
        }
        if result.error:
            payload["error"] = result.error
        self.store.set_condition_check(str(claim["id"]), payload)
        # Check cadence advances for every completed check, TRUE or FALSE.
        self.store.record_check_at(automation_id, stamp)

        if not result.ok:
            reason = f"condition evaluation failed: {result.error}"
            self.store.record_skip(automation_id, reason=reason)
            if self.review_sink is not None:
                self.review_sink(
                    automation_id,
                    str(definition.get("project_id", "")),
                    str(claim["id"]),
                    "condition_error",
                    reason,
                )
            return finish("error", "failed", reason)
        if result.value is False:
            return finish("false", "skipped", "condition evaluated false; no action executed")
        return None

    def worker_alive(self) -> bool:
        worker = self._thread
        return bool(worker and worker.is_alive())

    def status(self) -> dict[str, Any]:
        """Truthful observability snapshot (backend reachability excluded)."""

        alive = self.worker_alive()
        return {
            "running": alive,
            "worker_alive": alive,
            "started_at": self.started_at,
            "poll_interval_seconds": self.poll_seconds,
            "last_tick_at": self.last_tick_at,
            "last_tick_error": self.last_tick_error,
        }

    def stop(self, timeout: float | None = 5.0) -> bool:
        """Signal the worker to stop and join it within an optional timeout.

        Returns True when the worker thread is no longer running. Joining the
        calling thread from itself is refused (the signal still takes effect);
        a timeout expiry reports False truthfully so the caller can fall back
        to harder termination.
        """

        self._stop.set()
        worker = self._thread
        if worker is None or worker is threading.current_thread():
            return True
        if timeout is None:
            worker.join()
        else:
            worker.join(max(0.0, float(timeout)))
        stopped = not worker.is_alive()
        if self.audit is not None:
            if stopped:
                self.audit.record("scheduler_stopped")
            else:
                # Truthful: a timed-out stop must never be recorded as a
                # graceful completion.
                self.audit.record(
                    "scheduler_stop_timeout",
                    success=False,
                    detail=f"scheduler worker still running after {timeout}s graceful stop",
                )
        return stopped

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.tick()
                self.last_tick_error = None
            except Exception as error:
                # The scheduler must survive a bad definition; the failure is
                # visible here and in the returned per-automation records.
                self.last_tick_error = str(error)[:200]
                continue
            finally:
                self.last_tick_at = datetime.now(UTC).isoformat()

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = _naive_utc(now or self.clock())
        dispatched: list[dict[str, Any]] = []
        for definition in self.store.list():
            if not definition.get("enabled"):
                continue
            schedule = str(definition.get("schedule", ""))
            kind = str(definition.get("automation_type", ""))
            # Conditional-family cadence advances from the previous CHECK, not
            # only from the last actual action run (last_run_at stays honest).
            reference = definition.get("last_run_at")
            if kind in _CONDITIONAL_FAMILY and definition.get("last_check_at"):
                reference = definition.get("last_check_at")
            try:
                due_at = next_run_at(schedule, kind, reference, current)
            except ValueError as grammar_error:
                self.store.record_skip(
                    str(definition["id"]),
                    reason=str(grammar_error)[:200],
                )
                continue
            if due_at is None or current < due_at:
                continue
            try:
                slot = occurrence_slot(
                    schedule,
                    kind,
                    reference,
                    definition.get("created_at"),
                    current,
                )
            except ValueError as grammar_error:
                self.store.record_skip(
                    str(definition["id"]),
                    reason=str(grammar_error)[:200],
                )
                continue
            interval_match = re.match(r"^every\s+(\d+)([smh])$", schedule.strip().casefold())
            missed = False
            if interval_match:
                unit_seconds = {"s": 1, "m": 60, "h": 3600}[interval_match.group(2)]
                expected_gap = timedelta(seconds=int(interval_match.group(1)) * unit_seconds)
                reference = _parse_iso(definition.get("last_run_at"))
                missed = (
                    reference is not None
                    and (current - reference) > expected_gap * 2
                )
            # Durable claim commits before any dispatch; the store verifies
            # existence/enabled-ness on the freshest row inside the claim
            # transaction, so a disable landing after our snapshot wins.
            claim = self.store.claim_occurrence(
                str(definition["id"]),
                "" if slot is None else slot.isoformat(),
                now=current.isoformat(),
            )
            if claim is None:
                continue
            condition_result = None
            if kind in _CONDITIONAL_FAMILY:
                # B1.2B order: durable claim -> deterministic condition check
                # -> provenance -> dispatch only on TRUE. A disable/CAS loss
                # returns None from the claim above, so the evaluator is never
                # called for an automation the user just disabled.
                terminal_entry = self._check_condition(definition, kind, claim, current)
                if terminal_entry is not None:
                    dispatched.append(terminal_entry)
                    continue
                condition_result = "true"
            try:
                run_id = self.dispatch(goal=str(definition.get("goal", "")), project_id=str(definition.get("project_id", "")))
            except Exception as error:
                # Nothing executed while the process stayed alive: retain the
                # claim as an explicit 'dispatch refused' failure (provenance)
                # so the next tick may retry exactly this occurrence, and say
                # so honestly in the skip log.
                self.store.refuse_claim(str(claim["id"]), str(error))
                self.store.record_skip(
                    str(definition["id"]),
                    reason=f"dispatch refused: {error}"[:200],
                )
                if self.review_sink is not None:
                    self.review_sink(
                        str(definition["id"]),
                        str(definition.get("project_id", "")),
                        str(claim["id"]),
                        "dispatch_refused",
                        f"dispatch refused: {error}"[:200],
                    )
                continue
            self.store.mark_claim_dispatched(str(claim["id"]), run_id)
            # Bookkeeping uses the tick's logical clock so occurrence slots
            # stay deterministic under injected/test clocks.
            self.store.mark_run(str(definition["id"]), run_id, now=current.isoformat())
            if kind == "once":
                self.store.set_enabled(str(definition["id"]), False)
            if missed:
                self.store.merge_metadata(str(definition["id"]), {"missed": True})
            dispatched.append(
                {
                    "automation_id": definition["id"],
                    "run_id": run_id,
                    "missed": missed,
                    "occurrence_id": claim["id"],
                    "scheduled_for": claim["scheduled_for"],
                    "condition_result": condition_result,
                }
            )
        return dispatched


__all__ = ["AutomationScheduler", "next_run_at", "occurrence_slot"]
