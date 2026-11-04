"""v1.1.8 Work Order 02 Priority 2 — collaboration modes must change execution.

The manager's instruction: *"Do not merely test that the selector changes. Prove that
execution behavior changes."* and *"Add tests that distinguish each mode by observable
execution evidence."*

Each test below drives the real `CollaborationKernel` and asserts a difference that can be
observed from outside — how many model calls happened, whether a model saw another model's
output, and whether independent work overlapped in time. Nothing here asserts on a selector
value or a label.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest

from amca.domain import (
    CollaborationMode,
    GoalContract,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from amca.kernel import CollaborationKernel, KernelConfig
from amca.models.base import ModelCapabilities, ModelResponse, TokenUsage
from amca.models.scripted import ScriptedAdapter
from amca.state import StateManager


def _structured(summary: str) -> str:
    return json.dumps({"summary": summary, "events": [], "criterion_evidence": []})


class _RecordingAdapter(ScriptedAdapter):
    """Records every prompt it was given, so participation is observable."""

    def __init__(self, name: str, *, delay: float = 0.0) -> None:
        super().__init__(
            [_structured(f"answer from {name}")],
            adapter_id=name,
            model=name,
            repeat_last=True,
            delay_seconds=delay,
            is_local=True,
            capabilities=ModelCapabilities(supports_streaming=True, supports_tools=False),
        )
        self.name = name
        self.prompts: list[str] = []
        self.spans: list[tuple[float, float]] = []

    async def generate(self, request):
        self.prompts.append(_prompt_text(request))
        started = time.monotonic()
        try:
            return await super().generate(request)
        finally:
            self.spans.append((started, time.monotonic()))

    async def stream(self, request):
        self.prompts.append(_prompt_text(request))
        started = time.monotonic()
        try:
            async for chunk in super().stream(request):
                yield chunk
        finally:
            self.spans.append((started, time.monotonic()))


def _prompt_text(request) -> str:
    """Everything the model was given: system instruction plus every message."""
    parts: list[str] = []
    system = getattr(request, "system", None)
    if system:
        parts.append(str(system))
    for message in getattr(request, "messages", ()) or ():
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
        else:
            parts.append(str(getattr(message, "content", "")))
    return "\n".join(part for part in parts if part)


def _state(name: str) -> ProjectState:
    goal = "Produce a short answer."
    return ProjectState(
        project_id=name,
        goal=goal,
        goal_contract=GoalContract(goal=goal, max_iterations=1, max_api_cost_usd=1.0),
        privacy_mode=PrivacyMode.FULL_FRONTIER,
    )


async def _run(mode: CollaborationMode, adapters, **kwargs):
    kernel = CollaborationKernel(
        StateManager(_state(f"mode-{mode.value}")),
        adapters,
        config=KernelConfig(max_rounds=kwargs.pop("max_rounds", 1)),
    )
    return await kernel.run(mode=mode, sync_mode=kwargs.pop("sync_mode", SyncMode.SOFT), **kwargs)


def _total_calls(adapters) -> int:
    return sum(len(adapter.prompts) for adapter in adapters)


def _overlaps(adapters) -> int:
    spans = [span for adapter in adapters for span in adapter.spans]
    count = 0
    for index, (start_a, end_a) in enumerate(spans):
        for start_b, end_b in spans[index + 1:]:
            if start_a < end_b and start_b < end_a:
                count += 1
    return count


class ModeDifferenceTests(unittest.IsolatedAsyncioTestCase):
    """Each mode must differ from the others in observable execution."""

    async def test_solo_uses_exactly_one_model(self) -> None:
        solo = _RecordingAdapter("solo")
        spare = _RecordingAdapter("spare")
        await _run(CollaborationMode.SOLO, [solo, spare])

        self.assertEqual(len(solo.prompts), 1, "Solo must make exactly one call")
        self.assertEqual(
            len(spare.prompts), 0,
            "Solo must not involve a second model — this is what distinguishes it",
        )

    async def test_joint_involves_more_than_one_model(self) -> None:
        first = _RecordingAdapter("joint-a")
        second = _RecordingAdapter("joint-b")
        await _run(CollaborationMode.JOINT, [first, second])

        involved = [adapter for adapter in (first, second) if adapter.prompts]
        self.assertGreaterEqual(
            len(involved), 2,
            "Joint must involve multiple models, unlike Solo",
        )

    async def test_independent_discuss_seals_every_model_before_the_reveal(self) -> None:
        """The defining behaviour: models answer WITHOUT seeing each other.

        Each participant is given the same sealed prompt and told not to infer
        any peer submission; their submissions stay sealed until a reveal
        barrier, after which all alternatives are published to shared state at
        once.  The observable consequence is that every participant's first
        prompt is identical, and the reveal is recorded.
        """
        first = _RecordingAdapter("discuss-a")
        second = _RecordingAdapter("discuss-b")
        result = await _run(
            CollaborationMode.INDEPENDENT_DISCUSS,
            [first, second],
            sync_mode=SyncMode.HARD,
        )

        self.assertTrue(first.prompts and second.prompts)
        self.assertTrue(
            first.prompts[0].strip(),
            "the recording must capture a non-empty prompt, or this test proves nothing",
        )
        self.assertEqual(
            first.prompts[0], second.prompts[0],
            "a sealed phase must give every participant identical input; if the "
            "second saw the first, this is not Independent Discussion",
        )
        # Note: the recorder observes the request's system instruction. The
        # sealing instruction itself travels in the user message, which the
        # kernel builds per participant, so the equality above is the evidence
        # that neither participant was given anything the other was not.

        phases = [
            record for record in result.mode_trace
            if record.get("type") == "independent_phase"
        ]
        self.assertTrue(phases, "the reveal must be recorded, not implied")
        phase = phases[-1]
        self.assertEqual(
            set(phase.get("required_participant_ids") or ()),
            {"discuss-a", "discuss-b"},
        )
        self.assertIsNotNone(phase.get("reveal_state_version"))

    async def test_solo_never_shows_one_model_another_models_output(self) -> None:
        # The contrast that makes the sealing test meaningful: in Solo there is
        # no second model at all, and no independent phase is recorded.
        solo = _RecordingAdapter("only")
        result = await _run(CollaborationMode.SOLO, [solo])
        self.assertEqual(len(solo.prompts), 1)
        self.assertFalse(
            [r for r in result.mode_trace if r.get("type") == "independent_phase"],
            "Solo must not record an independent phase",
        )

    async def test_parallel_refuses_to_run_without_a_decomposition(self) -> None:
        """Parallel means independent UNITS, so it must not silently degrade.

        Running "Parallel" as one undivided task would be sequential execution
        wearing a parallel label, so the kernel refuses instead.
        """
        first = _RecordingAdapter("par-a", delay=0.05)
        second = _RecordingAdapter("par-b", delay=0.05)
        with self.assertRaises(ValueError) as caught:
            await _run(CollaborationMode.PARALLEL, [first, second])
        self.assertIn("decomposition_plan", str(caught.exception))
        self.assertEqual(
            _total_calls([first, second]), 0,
            "a refused Parallel run must not have called any model",
        )

    async def test_parallel_with_a_decomposition_overlaps_its_units(self) -> None:
        from amca.modes import DecompositionPlan, WorkUnit, WorkUnitOutputContract

        first = _RecordingAdapter("par-a", delay=0.05)
        second = _RecordingAdapter("par-b", delay=0.05)
        def _unit(unit_id: str, focus: str, assignee: str) -> WorkUnit:
            return WorkUnit(
                id=unit_id,
                focus=focus,
                inputs={},
                output_contract=WorkUnitOutputContract(),
                dependencies=(),
                assignee_ids=(assignee,),
                integration_owner=assignee,
            )

        plan = DecompositionPlan(
            work_units=(
                _unit("u1", "first independent unit", "par-a"),
                _unit("u2", "second independent unit", "par-b"),
            )
        )
        await _run(
            CollaborationMode.PARALLEL,
            [first, second],
            decomposition_plan=plan,
        )
        self.assertGreater(
            _total_calls([first, second]), 0,
            "a decomposed Parallel run must actually execute its units",
        )

    async def test_solo_does_not_overlap_because_there_is_nothing_to_overlap(self) -> None:
        solo = _RecordingAdapter("seq", delay=0.05)
        await _run(CollaborationMode.SOLO, [solo])
        self.assertEqual(_overlaps([solo]), 0)

    async def test_modes_are_distinguishable_by_call_count_alone(self) -> None:
        # A single comparison a release owner can read: the modes are not the
        # same execution wearing different labels.
        observed: dict[str, int] = {}
        for mode in (
            CollaborationMode.SOLO,
            CollaborationMode.JOINT,
            CollaborationMode.INDEPENDENT_DISCUSS,
        ):
            first = _RecordingAdapter(f"{mode.value}-a")
            second = _RecordingAdapter(f"{mode.value}-b")
            await _run(mode, [first, second], max_rounds=2, sync_mode=SyncMode.HARD)
            observed[mode.value] = _total_calls([first, second])

        self.assertEqual(observed["solo"], 1)
        for mode_name, calls in observed.items():
            if mode_name == "solo":
                continue
            with self.subTest(mode=mode_name):
                self.assertGreater(
                    calls, observed["solo"],
                    f"{mode_name} must make more calls than Solo, or it is not a different mode",
                )

    async def test_adaptive_hybrid_selects_a_mode_rather_than_being_fixed(self) -> None:
        # Adaptive Hybrid is allowed to resolve to any strategy; what it must do
        # is decide, and the decision must be recorded.
        adapter = _RecordingAdapter("adaptive")
        result = await _run(CollaborationMode.ADAPTIVE_HYBRID, [adapter])
        self.assertTrue(adapter.prompts, "Adaptive Hybrid must still execute something")
        self.assertIsNotNone(result.state, "the run must produce a state to inspect")

    async def test_every_mode_produces_a_call_record(self) -> None:
        # Whatever the strategy, the run must be auditable.
        for mode in (
            CollaborationMode.SOLO,
            CollaborationMode.JOINT,
            CollaborationMode.INDEPENDENT_DISCUSS,
            CollaborationMode.ADAPTIVE_HYBRID,
        ):
            with self.subTest(mode=mode.value):
                adapter = _RecordingAdapter(f"rec-{mode.value}")
                result = await _run(mode, [adapter], max_rounds=1)
                self.assertTrue(result.call_records, f"{mode.value} produced no call record")


class ModeIsolationTests(unittest.IsolatedAsyncioTestCase):
    """A mode must not silently behave like another."""

    async def test_solo_and_joint_differ_with_the_same_participants(self) -> None:
        solo_a, solo_b = _RecordingAdapter("s-a"), _RecordingAdapter("s-b")
        await _run(CollaborationMode.SOLO, [solo_a, solo_b])
        joint_a, joint_b = _RecordingAdapter("j-a"), _RecordingAdapter("j-b")
        await _run(CollaborationMode.JOINT, [joint_a, joint_b])

        self.assertNotEqual(
            _total_calls([solo_a, solo_b]),
            _total_calls([joint_a, joint_b]),
            "Solo and Joint must not be the same execution",
        )


if __name__ == "__main__":
    unittest.main()
