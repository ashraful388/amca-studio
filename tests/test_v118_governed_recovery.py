"""v1.1.8 — governed recovery policy (Manager architecture decision, Decision 1).

The directive is explicit that recovery must not be "task failed, rerun it on
another model": it must be governed by failure type, permissions, tool side
effects and the Goal Contract.  These tests pin the rules that make that true —
especially the refusals, which are the part that matters.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.models.scripted import ScriptedAdapter
from amca.governed_recovery import (
    ENVELOPE_TOTAL_BUDGET,
    MAX_ALTERNATE_MODEL_STEPS,
    RecoveryEnvelope,
    RecoveryAction,
    decide_recovery,
    RecoveryClass,
    RunOutcome,
    TaskCheckpoint,
    classify_failure,
    failure_report,
    is_fallback_eligible,
    outcome_for,
    select_fallback,
)
from amca.models import (
    ModelAuthenticationError,
    ModelCancelledError,
    ModelInvalidRequestError,
    ModelProtocolError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnavailableError,
)


class FailureClassificationTests(unittest.TestCase):
    """Which failures may be retried elsewhere, and which must not be."""

    def test_runtime_faults_may_be_recovered_elsewhere(self) -> None:
        for failure in (
            ModelTimeoutError("timed out"),
            TimeoutError("timed out"),
            ModelRateLimitError("rate limited"),
            ModelProviderUnavailableError("provider down"),
            ModelUnavailableError("unavailable"),
            ModelProtocolError("not valid JSON"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.assertTrue(is_fallback_eligible(failure))

    def test_a_permission_denial_is_never_recovered_elsewhere(self) -> None:
        # The refusal is the point: retrying on another model would either
        # bypass a control or be refused again for the same reason.
        self.assertFalse(is_fallback_eligible(PermissionError("denied")))

    def test_a_cancelled_run_is_never_recovered_elsewhere(self) -> None:
        # The user asked to stop.  Restarting on another model would ignore them.
        self.assertFalse(is_fallback_eligible(ModelCancelledError("cancelled by user")))

    def test_a_bad_credential_is_never_recovered_elsewhere(self) -> None:
        # A wrong key is not fixed by asking a different model.
        self.assertFalse(is_fallback_eligible(ModelAuthenticationError("401")))

    def test_an_invalid_request_is_never_recovered_elsewhere(self) -> None:
        # The request is wrong, not the model.
        self.assertFalse(is_fallback_eligible(ModelInvalidRequestError("400")))

    def test_an_invalid_task_definition_is_never_recovered_elsewhere(self) -> None:
        self.assertFalse(is_fallback_eligible(ValueError("invalid task definition")))

    def test_denial_wins_over_a_timeout_mentioned_in_the_same_message(self) -> None:
        # Ordering matters: a denial that happens to mention a timeout must
        # still refuse, or a control could be laundered into a retry.
        failure = PermissionError("permission denied after the provider timed out")
        self.assertEqual(
            classify_failure(failure), RecoveryClass.NOT_FALLBACK_ELIGIBLE
        )

    def test_an_unrecognised_failure_is_not_recovered(self) -> None:
        # An unknown fault is not evidence that another model behaves better.
        class SomethingNew(Exception):
            pass

        self.assertFalse(is_fallback_eligible(SomethingNew("no idea")))

    def test_approval_and_tool_denials_are_not_recovered(self) -> None:
        for reason in (
            "approval denied by the user",
            "tool denied by policy",
            "policy violation",
            "capability impossible for this goal",
        ):
            with self.subTest(reason=reason):
                self.assertFalse(is_fallback_eligible(reason))

    def test_transient_reasons_are_recognised_from_text_too(self) -> None:
        # The scheduler records status strings as well as raising, so both must
        # classify identically.
        for reason in ("timed out", "rate limit exceeded", "could not reach provider"):
            with self.subTest(reason=reason):
                self.assertTrue(is_fallback_eligible(reason))


class FallbackSelectionTests(unittest.TestCase):
    """A replacement must satisfy EVERY requirement, not merely respond."""

    CANDIDATES = [
        {"model_ref": "openai:gpt-4o", "supports_tools": True, "context_window": 128000,
         "is_local": False, "visible": True, "available": True, "expected_cost_usd": 0.5},
        {"model_ref": "ollama:qwen3:1.7b", "supports_tools": False, "context_window": 32000,
         "is_local": True, "visible": True, "available": True, "expected_cost_usd": 0.0},
    ]

    def test_the_failed_model_is_excluded(self) -> None:
        chosen, reason, considered = select_fallback(
            self.CANDIDATES, failed_model="openai:gpt-4o",
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["model_ref"], "ollama:qwen3:1.7b")
        self.assertNotIn("openai:gpt-4o", considered)

    def test_a_previously_tried_model_is_not_retried(self) -> None:
        # Otherwise recovery could bounce between the same two providers.
        chosen, reason, _ = select_fallback(
            self.CANDIDATES,
            failed_model="openai:gpt-4o",
            already_tried=("ollama:qwen3:1.7b",),
        )
        self.assertIsNone(chosen)
        self.assertIn("no candidate", reason)

    def test_a_tool_requirement_excludes_a_model_without_tools(self) -> None:
        chosen, reason, _ = select_fallback(
            self.CANDIDATES, failed_model="openai:gpt-4o",
            requirements={"supports_tools": True},
        )
        self.assertIsNone(chosen)
        self.assertIn("no eligible", reason)

    def test_a_locality_requirement_is_honoured(self) -> None:
        chosen, _reason, _ = select_fallback(
            self.CANDIDATES, failed_model="some-cloud-model",
            requirements={"requires_local": True},
        )
        self.assertIsNotNone(chosen)
        self.assertTrue(chosen["is_local"])

    def test_a_hidden_model_is_never_selected(self) -> None:
        # Visibility is a user control, not a routing hint: a model the user has
        # hidden must not be chosen as a fallback even if it is the only one
        # that satisfies every capability requirement.
        candidates = [
            {"model_ref": "openai:gpt-4o", "supports_tools": True, "context_window": 128000,
             "is_local": False, "visible": False, "available": True, "expected_cost_usd": 0.5},
        ]
        chosen, reason, _ = select_fallback(
            candidates, failed_model="x", requirements={"supports_tools": True},
        )
        self.assertIsNone(chosen)
        self.assertIn("no eligible", reason)

    def test_an_unavailable_model_is_never_selected(self) -> None:
        candidates = [
            {"model_ref": "openai:gpt-4o", "supports_tools": True, "context_window": 128000,
             "is_local": False, "visible": True, "available": False, "expected_cost_usd": 0.5},
        ]
        chosen, _reason, _ = select_fallback(candidates, failed_model="x")
        self.assertIsNone(chosen)

    def test_a_cost_policy_excludes_an_expensive_model(self) -> None:
        chosen, reason, _ = select_fallback(
            self.CANDIDATES, failed_model="x",
            requirements={"max_cost_usd": 0.01},
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["model_ref"], "ollama:qwen3:1.7b")

    def test_no_candidates_says_so_rather_than_guessing(self) -> None:
        chosen, reason, considered = select_fallback([], failed_model="a")
        self.assertIsNone(chosen)
        self.assertEqual(considered, ())
        self.assertIn("no candidate", reason)

    def test_the_recovery_is_bounded_to_one_step(self) -> None:
        self.assertEqual(MAX_ALTERNATE_MODEL_STEPS, 1)


class SideEffectSafetyTests(unittest.TestCase):
    """A task that already changed something must not be blindly replayed."""

    def test_a_task_with_no_side_effects_is_safe_to_rerun(self) -> None:
        checkpoint = TaskCheckpoint(task_id="t1", completed_model_steps=2)
        self.assertTrue(checkpoint.safe_to_rerun)

    def test_a_task_that_wrote_something_is_not_safe_to_rerun(self) -> None:
        checkpoint = TaskCheckpoint(
            task_id="t1", completed_tool_executions=("filesystem.write",),
            side_effects_performed=True,
        )
        self.assertFalse(checkpoint.safe_to_rerun)

    def test_recovery_context_preserves_work_and_forbids_repetition(self) -> None:
        checkpoint = TaskCheckpoint(
            task_id="t1", completed_model_steps=3,
            completed_tool_executions=("filesystem.write",),
            artifact_versions=(("report", 2),),
            side_effects_performed=True, pending_step="publish",
        )
        context = checkpoint.recovery_context()
        self.assertIn("t1", context)
        self.assertIn("report@v2", context)
        self.assertIn("must not be repeated", context)
        self.assertIn("pending_step: publish", context)

    def test_a_clean_checkpoint_carries_no_repetition_warning(self) -> None:
        context = TaskCheckpoint(task_id="t1").recovery_context()
        self.assertNotIn("must not be repeated", context)


class OutcomeClassificationTests(unittest.TestCase):
    """Three distinct states — a partial answer must not be manufactured."""

    def test_everything_required_completed_is_complete(self) -> None:
        self.assertIs(
            outcome_for(required_tasks=["a", "b"], completed_tasks=["a", "b"]),
            RunOutcome.COMPLETE,
        )

    def test_some_branches_succeeded_is_degraded(self) -> None:
        self.assertIs(
            outcome_for(required_tasks=["a", "b"], completed_tasks=["a"]),
            RunOutcome.DEGRADED,
        )

    def test_nothing_substantive_succeeded_is_failed(self) -> None:
        # The exact shape of the real coding run: the first task failed and
        # every dependent was cancelled.  There is no partial answer to give.
        self.assertIs(
            outcome_for(
                required_tasks=["understand", "analyze-1", "verify"],
                completed_tasks=[],
                cancelled_tasks=["analyze-1", "verify"],
            ),
            RunOutcome.FAILED,
        )


class FailureReportTests(unittest.TestCase):
    """A failed run must never resolve to an empty message."""

    def test_the_report_names_the_cause_and_the_recovery_state(self) -> None:
        report = failure_report(
            run_status="failed", failed_task="understand", model="ollama:qwen3:1.7b",
            reason="timed out", attempts=2, fallback_attempted=True,
            fallback_model="", completed_tasks=[],
            cancelled_tasks=["analyze-1", "critique", "verify", "synthesize"],
        )
        self.assertIn("Run failed", report)
        self.assertIn("understand", report)
        self.assertIn("timed out", report)
        self.assertIn("Attempts: 2", report)
        self.assertIn("Fallback attempted: yes", report)
        self.assertIn("Cancelled dependent tasks: 4", report)

    def test_a_report_is_never_empty(self) -> None:
        self.assertTrue(failure_report(run_status="failed").strip())

    def test_the_report_is_readable_not_a_debug_dump(self) -> None:
        report = failure_report(run_status="failed", failed_task="t", reason="timed out")
        self.assertNotIn("{", report)
        self.assertNotIn("Traceback", report)


class _CountingAdapter(ScriptedAdapter):
    """A replacement model that records whether it was ever called."""

    def __init__(self, text: str = "recovered") -> None:
        super().__init__([text], adapter_id="fallback", model="fallback",
                         is_local=True, repeat_last=True)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return await super().generate(request)


class RecoveryActionPreferenceTests(unittest.TestCase):
    """Work Order 09 section 3 — resume is preferred over replay.

        Can safely continue from existing verified state? yes -> RESUME
        no -> Can task safely be replayed? yes -> RETRY/FALLBACK
                                          no  -> FAIL truthfully
    """

    def test_verified_work_with_a_remaining_step_resumes(self) -> None:
        checkpoint = TaskCheckpoint(
            task_id="t1", completed_model_steps=2,
            completed_tool_executions=("filesystem.write",),
            side_effects_performed=True, pending_step="publish",
        )
        action, reason = decide_recovery(checkpoint, ("filesystem.write",))
        self.assertIs(action, RecoveryAction.RESUME)
        self.assertIn("remaining step", reason)

    def test_a_task_with_no_preserved_work_and_no_risk_may_replay(self) -> None:
        checkpoint = TaskCheckpoint(task_id="t1")
        action, reason = decide_recovery(checkpoint, ("filesystem.read",))
        self.assertIs(action, RecoveryAction.REPLAY)
        self.assertIn("nothing irreversible", reason)

    def test_a_task_with_an_irreversible_effect_and_no_resume_point_fails(self) -> None:
        # The dangerous combination: something already happened, and there is
        # no named step to continue from.  Replaying would repeat the effect.
        checkpoint = TaskCheckpoint(
            task_id="t1", completed_tool_executions=("filesystem.write",),
            side_effects_performed=True, pending_step="",
        )
        action, reason = decide_recovery(checkpoint, ("filesystem.write",))
        self.assertIs(action, RecoveryAction.FAIL)
        self.assertIn("failing truthfully", reason)

    def test_an_unknown_tool_forces_fail_rather_than_replay(self) -> None:
        action, _reason = decide_recovery(TaskCheckpoint(task_id="t1"), ("mystery.tool",))
        self.assertIs(action, RecoveryAction.FAIL)

    def test_resume_is_chosen_before_replay_when_both_are_possible(self) -> None:
        # Order matters: replaying a task that holds verified work throws that
        # work away and pays for it twice.
        checkpoint = TaskCheckpoint(
            task_id="t1", completed_model_steps=1, pending_step="continue",
        )
        action, _reason = decide_recovery(checkpoint, ("filesystem.read",))
        self.assertIs(action, RecoveryAction.RESUME)


class RecoveryEnvelopeTests(unittest.TestCase):
    """Work Order 09 section 4 — the full, bounded recovery envelope.

    Rules: ONE total budget (not per item), structured references rather than
    raw content, and no rebuild from the chat transcript.
    """

    def _envelope(self, **overrides):
        fields = dict(
            run_id="org-1", task_id="t1", goal_contract_summary="Write a CSV parser",
            active_focus="parse rows", original_model="ollama:qwen3:1.7b",
            failure_class="retriable_on_another_model", failure_reason="timed out",
            attempt_count=2, required_capabilities=("coding",),
            verified_completed_work=("step one done",),
            safe_tool_results=(("filesystem.read", "abc123"),),
            artifact_versions=(("report", 2),), pending_step="finish the parser",
            side_effects_performed=True, action="resume",
        )
        fields.update(overrides)
        return RecoveryEnvelope(**fields)

    def test_the_envelope_carries_the_task_definition_half(self) -> None:
        # The half that was missing: who the run is, what the goal was, what
        # capabilities it needs, and which model failed.
        text = self._envelope().render()
        for fragment in ("org-1", "t1", "Write a CSV parser", "parse rows",
                         "ollama:qwen3:1.7b", "retriable_on_another_model", "timed out"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_the_envelope_carries_the_execution_half(self) -> None:
        text = self._envelope().render()
        for fragment in ("step one done", "filesystem.read", "report@v2",
                         "finish the parser", "side_effects_performed: True"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_a_resumed_envelope_forbids_repeating_completed_work(self) -> None:
        self.assertIn("must not be repeated", self._envelope().render())

    def test_a_clean_envelope_carries_no_repetition_warning(self) -> None:
        self.assertNotIn("must not be repeated",
                         self._envelope(side_effects_performed=False).render())

    def test_the_whole_envelope_is_bounded_by_one_total_budget(self) -> None:
        # The failure mode this guards: each item consuming its own maximum,
        # so a "bounded" envelope grows without limit as the graph widens.
        wide = self._envelope(
            verified_completed_work=tuple(f"step {i} " + "x" * 400 for i in range(40)),
            safe_tool_results=tuple((f"tool.{i}", "d" * 64) for i in range(40)),
        )
        rendered = wide.render()
        self.assertLessEqual(len(rendered), ENVELOPE_TOTAL_BUDGET)
        self.assertIn("budget exhausted", rendered)

    def test_a_withheld_section_says_so_rather_than_truncating_silently(self) -> None:
        wide = self._envelope(
            verified_completed_work=tuple(f"step {i} " + "x" * 400 for i in range(40)),
        )
        self.assertIn("withheld", wide.render())

    def test_serialisation_exposes_every_field(self) -> None:
        payload = self._envelope().to_dict()
        for field in ("run_id", "task_id", "goal_contract_summary", "active_focus",
                      "task_requirements", "required_capabilities", "original_model",
                      "failure_class", "failure_reason", "attempt_count",
                      "start_state_version", "current_state_version",
                      "verified_completed_work", "safe_tool_results",
                      "artifact_versions", "pending_step", "side_effects_performed",
                      "action"):
            with self.subTest(field=field):
                self.assertIn(field, payload)

    def test_the_envelope_is_built_from_records_not_from_a_transcript(self) -> None:
        # Section 4 rule 3: no raw transcript.  The envelope has no field for
        # message history at all.
        payload = self._envelope().to_dict()
        for forbidden in ("messages", "transcript", "history", "chat"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, payload)


class SchedulerRecoveryIntegrationTests(unittest.TestCase):
    """The policy must actually fire in the scheduler, not sit unused."""

    def _run(self, failure, *, fallback=None, tool_calls=()):
        import asyncio
        from amca.agent_runtime import AgentTaskRecord, ExecutorKind, NanoScheduler, OrganizationRun
        from amca.models.scripted import ScriptedAdapter

        class _Fails(ScriptedAdapter):
            def __init__(self, name):
                super().__init__(["unused"], adapter_id=name, model=name, is_local=True,
                                 repeat_last=True)
                self.name = name
                self.calls = 0

            async def generate(self, request):
                self.calls += 1
                raise failure

        primary = _Fails("primary")
        replacement = None
        adapters = {"agent.a": primary}
        fallback_map = {}
        if fallback is not None:
            replacement = fallback(primary)
            fallback_map = {"agent.a": replacement}

        scheduler = NanoScheduler(
            adapters, budget_usd=0.0, max_concurrent=1,
            fallback_adapters_by_id=fallback_map,
        )
        org = scheduler.schedule(
            [{"id": "t1", "agent_id": "agent.a", "focus": "work",
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "depends_on": []}],
            organization_id="o", project_id="p", goal="g",
        )
        if tool_calls:
            # Side effects are tracked by the scheduler when a tool dispatches;
            # seed it directly here so the test does not need a live dispatcher.
            scheduler.executed_tools_by_task["t1"] = list(tool_calls)
        asyncio.run(scheduler.run(org))
        return scheduler, org, primary, replacement

    def test_a_timeout_triggers_one_alternate_model_step(self) -> None:
        from amca.models import ModelTimeoutError
        from amca.models.scripted import ScriptedAdapter

        scheduler, org, primary, replacement_adapter = self._run(
            ModelTimeoutError("timed out"), fallback=lambda _p: _CountingAdapter())
        self.assertTrue(scheduler.recovery_records, "the recovery must be recorded")
        record = scheduler.recovery_records[0]
        self.assertEqual(record["failure_class"], "retriable_on_another_model")
        self.assertTrue(record["fallback_requested"])
        self.assertEqual(record["selected_fallback"], "fallback")
        self.assertGreater(replacement_adapter.calls, 0, "the alternate model must actually run")

    def test_a_permission_denial_never_triggers_a_model_switch(self) -> None:
        from amca.models.scripted import ScriptedAdapter

        scheduler, org, primary, replacement_adapter = self._run(
            PermissionError("permission denied"), fallback=lambda _p: _CountingAdapter())
        record = scheduler.recovery_records[0]
        self.assertEqual(record["failure_class"], "not_fallback_eligible")
        self.assertFalse(record["fallback_requested"])
        self.assertEqual(replacement_adapter.calls, 0,
                         "a denial must not be laundered into a retry elsewhere")

    def test_a_cancellation_never_triggers_a_model_switch(self) -> None:
        from amca.models import ModelCancelledError
        from amca.models.scripted import ScriptedAdapter

        scheduler, _org, _primary, replacement_adapter = self._run(
            ModelCancelledError("cancelled by user"), fallback=lambda _p: _CountingAdapter())
        self.assertFalse(scheduler.recovery_records[0]["fallback_requested"])
        self.assertEqual(replacement_adapter.calls, 0)

    def test_a_side_effecting_task_is_resumed_not_replayed(self) -> None:
        from amca.models import ModelTimeoutError

        scheduler, org, _primary, replacement_adapter = self._run(
            ModelTimeoutError("timed out"), fallback=lambda _p: _CountingAdapter(),
            tool_calls=("filesystem.write",))
        record = scheduler.recovery_records[0]
        # Work Order 09 section 3: resume is preferred over replay.  The task is
        # continued from its preserved state, and the recovering model is told
        # not to repeat work that already happened.
        self.assertEqual(record["action"], "resume")
        self.assertIn("remaining step", record["selection_reason"])
        self.assertIn("must not be repeated", org.tasks["t1"].focus)
        self.assertIn("side_effects_performed: True", org.tasks["t1"].focus)

    def test_no_fallback_configured_is_recorded_not_silent(self) -> None:
        from amca.models import ModelTimeoutError

        scheduler, _org, _primary, _replacement = self._run(ModelTimeoutError("timed out"))
        record = scheduler.recovery_records[0]
        self.assertTrue(record["fallback_requested"] is False)
        self.assertIn("no eligible replacement", record["selection_reason"])

    def test_recovery_is_bounded_to_one_step_per_task(self) -> None:
        from amca.models import ModelTimeoutError
        from amca.models.scripted import ScriptedAdapter

        class _AlsoFails(ScriptedAdapter):
            def __init__(self):
                super().__init__(["unused"], adapter_id="fallback", model="fallback",
                                 is_local=True, repeat_last=True)
                self.calls = 0

            async def generate(self, request):
                self.calls += 1
                raise ModelTimeoutError("timed out again")

        scheduler, org, _primary, replacement_adapter = self._run(
            ModelTimeoutError("timed out"), fallback=lambda _p: _AlsoFails())
        # The replacement ran, then failed; there must be no third model.
        self.assertGreater(replacement_adapter.calls, 0)
        self.assertLessEqual(len(scheduler.recovery_records), 2)
        self.assertEqual(str(org.tasks["t1"].status), "failed")


GOAL = "Write and review a Python module with unit tests"


class ServiceRecoveryReportingTests(unittest.TestCase):
    """The failure report must survive the path that Case A never exercises.

    A real verification run found a KeyError here: the report builder read
    "result" from the scheduler INPUT dicts, which do not carry it.  Case A
    masked it entirely because a fully successful run never builds a report.
    """

    def _context(self, tmp):
        from amca.service import ServiceContext

        software_root = Path(__file__).resolve().parents[1]
        return ServiceContext(
            root=software_root, database=tmp / "t.db",
            benchmarks=software_root / "benchmarks" / "tasks.json",
        )

    def test_a_run_with_no_fallback_reports_failure_without_crashing(self) -> None:
        from amca.models import ModelTimeoutError
        from amca.models.scripted import ScriptedAdapter

        class _AlwaysTimesOut(ScriptedAdapter):
            def __init__(self):
                super().__init__(["unused"], adapter_id="primary", model="primary",
                                 is_local=True, repeat_last=True)

            async def generate(self, request):
                raise ModelTimeoutError("timed out")

        tmp = Path(tempfile.mkdtemp())
        out = self._context(tmp).agent_plan_execute(
            {"project_id": "p1", "goal": GOAL, "participant": "unused",
             "_test_adapter": _AlwaysTimesOut()}
        )
        # The KeyError this test exists for would have raised before reaching here.
        self.assertEqual(out["outcome"], "failed")
        self.assertTrue(out["failure_report"].strip())
        self.assertIn("Run failed", out["failure_report"])
        self.assertEqual(out["tasks"][0]["status"], "failed")
        self.assertTrue(
            all(task["status"] == "cancelled" for task in out["tasks"][1:]),
            "dependents of a failed task must be cancelled, not left running",
        )
        self.assertEqual(out["failure_report"].count("Cancelled dependent tasks: "), 1)

    def test_a_failed_run_never_reports_complete(self) -> None:
        # The defect the manager named: an apparently blank COMPLETED turn.
        from amca.models import ModelTimeoutError
        from amca.models.scripted import ScriptedAdapter

        class _AlwaysTimesOut(ScriptedAdapter):
            def __init__(self):
                super().__init__(["unused"], adapter_id="primary", model="primary",
                                 is_local=True, repeat_last=True)

            async def generate(self, request):
                raise ModelTimeoutError("timed out")

        tmp = Path(tempfile.mkdtemp())
        out = self._context(tmp).agent_plan_execute(
            {"project_id": "p1", "goal": GOAL, "participant": "unused",
             "_test_adapter": _AlwaysTimesOut()}
        )
        produced = [task for task in out["tasks"] if task["result"]]
        self.assertFalse(
            out["outcome"] == "complete" and not produced,
            "a run with no output must not be reported as complete",
        )

    def test_a_denial_is_reported_and_never_recovered(self) -> None:
        from amca.models.scripted import ScriptedAdapter

        class _Denied(ScriptedAdapter):
            def __init__(self):
                super().__init__(["unused"], adapter_id="primary", model="primary",
                                 is_local=True, repeat_last=True)

            async def generate(self, request):
                raise PermissionError("permission denied")

        tmp = Path(tempfile.mkdtemp())
        out = self._context(tmp).agent_plan_execute(
            {"project_id": "p1", "goal": GOAL, "participant": "unused",
             "_test_adapter": _Denied(),
             "_test_fallback_adapter": ScriptedAdapter(
                 ["should not run"], adapter_id="fb", model="fb", is_local=True,
                 repeat_last=True),
             }
        )
        record = out["recovery"][0]
        self.assertEqual(record["failure_class"], "not_fallback_eligible")
        self.assertFalse(record["fallback_requested"])

    def test_a_fallback_participant_cannot_be_the_primary(self) -> None:
        # Falling back to the model that just failed is not a fallback.
        from amca.models.scripted import ScriptedAdapter

        tmp = Path(tempfile.mkdtemp())
        with self.assertRaises(ValueError) as caught:
            self._context(tmp).agent_plan_execute(
                {"project_id": "p1", "goal": GOAL, "participant": "ollama:qwen3:1.7b",
                 "fallback_participant": "ollama:qwen3:1.7b",
                 "_test_adapter": ScriptedAdapter(["x"], adapter_id="p", is_local=True)}
            )
        self.assertIn("must differ", str(caught.exception))


class RecoveryEventTests(unittest.TestCase):
    """Work Order 09 section 5 — recovery must be visible to the kernel.

    Recovery records living only on the scheduler would make recovery a hidden
    mechanism.  Every step is published on the same event channel the kernel
    already consumes for tool calls and turn completion.
    """

    def _events_for(self, failure, *, fallback=None, tool_calls=()):
        import asyncio
        from amca.agent_runtime import ExecutorKind, NanoScheduler
        from amca.models.scripted import ScriptedAdapter

        events: list[tuple[str, dict]] = []

        class _Fails(ScriptedAdapter):
            def __init__(self, name):
                super().__init__(["unused"], adapter_id=name, model=name, is_local=True,
                                 repeat_last=True)

            async def generate(self, request):
                raise failure

        primary = _Fails("primary")
        adapters = {"agent.a": primary}
        fallback_map = {}
        if fallback is not None:
            fallback_map = {"agent.a": fallback(primary)}

        scheduler = NanoScheduler(
            adapters, budget_usd=0.0, max_concurrent=1,
            fallback_adapters_by_id=fallback_map,
            event_sink=lambda name, payload: events.append((name, dict(payload))),
        )
        org = scheduler.schedule(
            [{"id": "t1", "agent_id": "agent.a", "focus": "work",
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "depends_on": []}],
            organization_id="o", project_id="p", goal="g",
        )
        if tool_calls:
            scheduler.executed_tools_by_task["t1"] = list(tool_calls)
        asyncio.run(scheduler.run(org))
        return events, scheduler

    def test_a_failure_is_published_even_when_nothing_is_recovered(self) -> None:
        from amca.models import ModelTimeoutError

        events, _scheduler = self._events_for(ModelTimeoutError("timed out"))
        names = [name for name, _payload in events]
        self.assertIn("recovery.MODEL_FAILURE", names)
        self.assertIn("recovery.TASK_FAILED", names)

    def test_a_denial_is_published_as_a_failure_not_a_recovery(self) -> None:
        from amca.models.scripted import ScriptedAdapter

        events, _scheduler = self._events_for(
            PermissionError("permission denied"),
            fallback=lambda _p: ScriptedAdapter(["x"], adapter_id="fb", is_local=True),
        )
        names = [name for name, _payload in events]
        self.assertIn("recovery.MODEL_FAILURE", names)
        self.assertIn("recovery.TASK_FAILED", names)
        self.assertNotIn("recovery.FALLBACK_SELECTED", names,
                         "a denial must never be published as a recovery")

    def test_a_recovery_publishes_requested_selected_and_recovered(self) -> None:
        from amca.models import ModelTimeoutError
        from amca.models.scripted import ScriptedAdapter

        def replacement(_primary):
            return ScriptedAdapter(["recovered"], adapter_id="fallback", model="fallback",
                                   is_local=True, repeat_last=True)

        events, _scheduler = self._events_for(
            ModelTimeoutError("timed out"), fallback=replacement)
        names = [name for name, _payload in events]
        self.assertIn("recovery.FALLBACK_REQUESTED", names)
        self.assertIn("recovery.FALLBACK_SELECTED", names)
        self.assertIn("recovery.TASK_RECOVERED", names,
                      "the kernel should see that recovery WORKED, not just that it was tried")

    def test_events_carry_the_task_and_the_fallback_identity(self) -> None:
        from amca.models import ModelTimeoutError
        from amca.models.scripted import ScriptedAdapter

        def replacement(_primary):
            return ScriptedAdapter(["recovered"], adapter_id="fallback", model="fallback",
                                   is_local=True, repeat_last=True)

        events, _scheduler = self._events_for(
            ModelTimeoutError("timed out"), fallback=replacement)
        selected = next(payload for name, payload in events
                        if name == "recovery.FALLBACK_SELECTED")
        self.assertEqual(selected["task_id"], "t1")
        self.assertEqual(selected["selected_fallback"], "fallback")

    def test_a_broken_event_sink_does_not_take_the_run_down(self) -> None:
        # Losing the event is a visibility loss; it must not be a correctness one.
        import asyncio
        from amca.agent_runtime import ExecutorKind, NanoScheduler
        from amca.models import ModelTimeoutError
        from amca.models.scripted import ScriptedAdapter

        class _Fails(ScriptedAdapter):
            def __init__(self):
                super().__init__(["unused"], adapter_id="primary", model="primary",
                                 is_local=True, repeat_last=True)

            async def generate(self, request):
                raise ModelTimeoutError("timed out")

        def broken_sink(_name, _payload):
            raise RuntimeError("sink is down")

        scheduler = NanoScheduler(
            {"agent.a": _Fails()}, budget_usd=0.0, max_concurrent=1, event_sink=broken_sink,
        )
        org = scheduler.schedule(
            [{"id": "t1", "agent_id": "agent.a", "focus": "work",
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "depends_on": []}],
            organization_id="o", project_id="p", goal="g",
        )
        asyncio.run(scheduler.run(org))
        self.assertTrue(scheduler.recovery_records,
                        "the record survives even when the sink fails")


if __name__ == "__main__":
    unittest.main()
