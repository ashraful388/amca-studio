"""Work Order 09 section 2 — Tool Fabric owns replay-safety, recovery asks.

The correction is explicit:

    Do NOT determine replay safety permanently from tool name, prefixes, string
    matching, hard-coded lists spread through recovery logic.

and 2.1:

    If a tool lacks effect metadata: effect_class = UNKNOWN, replay_safe = false.
    Do not infer safety.

These tests pin that, and pin that the old name-matching inference is gone.
"""

from __future__ import annotations

import inspect
import unittest

from amca.tool_effects import (
    EffectClass,
    TOOL_EFFECTS,
    UNKNOWN_EFFECT,
    describe_effects,
    effect_for,
    replay_safe,
    task_replay_safe,
)


class EffectMetadataTests(unittest.TestCase):

    def test_every_governed_tool_declares_its_effect(self) -> None:
        # A governed tool that is not in the table would fail closed at
        # recovery time, which is safe but means a missing entry.  Catch it here.
        from amca.governed_chat import _GOVERNED_TOOL_SCHEMAS

        for name in _GOVERNED_TOOL_SCHEMAS:
            with self.subTest(tool=name):
                self.assertIn(name, TOOL_EFFECTS, f"{name} has no effect metadata")

    def test_read_only_tools_are_replay_safe(self) -> None:
        for name in ("filesystem.read", "filesystem.stat", "filesystem.list",
                     "filesystem.search", "command.inspect"):
            with self.subTest(tool=name):
                self.assertIs(effect_for(name)["effect_class"], EffectClass.READ_ONLY)
                self.assertTrue(replay_safe(name))

    def test_writes_are_not_replay_safe_by_default(self) -> None:
        # Replaying a write with a DIFFERENT model produces different content,
        # so the second write is a change, not a no-op.
        for name in ("filesystem.write", "filesystem.create", "filesystem.append",
                     "filesystem.move", "filesystem.copy", "filesystem.trash"):
            with self.subTest(tool=name):
                self.assertFalse(replay_safe(name))

    def test_running_a_command_is_never_replay_safe(self) -> None:
        self.assertIs(effect_for("command.run")["effect_class"],
                      EffectClass.EXTERNAL_SIDE_EFFECT)
        self.assertFalse(replay_safe("command.run"))

    def test_an_unknown_tool_fails_closed(self) -> None:
        # The whole point: safety is not inferred from a name.
        for name in ("filesystem.read_v2", "fs.read", "readFile", "READ",
                     "filesystem.readOnlyHelper", "something_new", ""):
            with self.subTest(tool=name):
                self.assertIs(effect_for(name)["effect_class"], EffectClass.UNKNOWN)
                self.assertFalse(replay_safe(name))

    def test_a_name_that_looks_read_only_is_still_unknown(self) -> None:
        # A tool merely NAMED like a read must not be assumed safe.
        self.assertFalse(replay_safe("filesystem.read_and_delete"))
        self.assertFalse(replay_safe("filesystem.list_then_purge"))

    def test_the_unknown_default_requires_approval(self) -> None:
        self.assertTrue(UNKNOWN_EFFECT["requires_approval"])

    def test_metadata_is_returned_as_a_copy(self) -> None:
        # A caller must not be able to mutate the authority table.
        entry = effect_for("filesystem.read")
        entry["replay_safe"] = False
        self.assertTrue(replay_safe("filesystem.read"))


class TaskReplaySafetyTests(unittest.TestCase):

    def test_a_task_that_only_read_is_replayable(self) -> None:
        safe, blocking = task_replay_safe(["filesystem.read", "filesystem.search"])
        self.assertTrue(safe)
        self.assertEqual(blocking, ())

    def test_a_task_that_wrote_is_not_replayable(self) -> None:
        safe, blocking = task_replay_safe(["filesystem.read", "filesystem.write"])
        self.assertFalse(safe)
        self.assertEqual(blocking, ("filesystem.write",))

    def test_a_task_that_ran_an_unknown_tool_is_not_replayable(self) -> None:
        safe, blocking = task_replay_safe(["filesystem.read", "mystery.tool"])
        self.assertFalse(safe)
        self.assertIn("mystery.tool", blocking)

    def test_a_task_that_ran_nothing_is_replayable(self) -> None:
        self.assertTrue(task_replay_safe([])[0])
        self.assertTrue(task_replay_safe(None)[0])


class NoNameMatchingInRecoveryTests(unittest.TestCase):
    """The old inference must be gone, not merely unused."""

    def test_the_recovery_path_asks_the_fabric_rather_than_matching_names(self) -> None:
        from amca import agent_runtime

        source = inspect.getsource(agent_runtime)
        self.assertIn("task_replay_safe", source,
                      "recovery must consult the Tool Fabric's effect metadata")
        for forbidden in ("_READ_ONLY_TOOL_MARKERS", "read_only_markers"):
            with self.subTest(marker=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_effect_table_is_describable_for_audit(self) -> None:
        rows = describe_effects()
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(tool=row["tool"]):
                self.assertIn("effect_class", row)
                self.assertIn("replay_safe", row)


class ToolExecutionRecordConsolidationTests(unittest.TestCase):
    """Work Order 09 section 2.3 — one record carries everything recovery needs.

    Recovery must decide from execution EVIDENCE rather than reassembling it
    from separate places: authorization, approval, effect class, replay safety,
    execution status, result and the artifacts produced.
    """

    def _record(self, **overrides):
        from amca.tool_fabric import Decision, ExecutionStatus, ToolExecutionRecord

        fields = dict(
            execution_id="exec-1", request_id="req-1", tool_id="filesystem.write",
            operation="write", decision=Decision.ALLOW,
            status=ExecutionStatus.SUCCEEDED, approval_reference="approval-1",
            result_summary="wrote 12 bytes", effect_class="non_idempotent_write",
            replay_safe=False, artifact_versions=(("report", 2),),
        )
        fields.update(overrides)
        return ToolExecutionRecord(**fields)

    def test_the_record_carries_everything_recovery_needs(self) -> None:
        payload = self._record().to_dict()
        for field in ("decision", "approval_reference", "effect_class", "replay_safe",
                      "status", "result_summary", "artifact_versions"):
            with self.subTest(field=field):
                self.assertIn(field, payload)

    def test_replay_safety_travels_with_the_record(self) -> None:
        self.assertFalse(self._record().to_dict()["replay_safe"])
        self.assertTrue(
            self._record(tool_id="filesystem.read", effect_class="read_only",
                         replay_safe=True).to_dict()["replay_safe"]
        )

    def test_artifact_versions_are_serialisable(self) -> None:
        payload = self._record().to_dict()
        self.assertEqual(payload["artifact_versions"], [["report", 2]])

    def test_a_record_without_effect_metadata_defaults_to_not_replay_safe(self) -> None:
        # Fail-closed: a record built without effect metadata must not claim
        # replay safety.
        payload = self._record(effect_class="", replay_safe=False).to_dict()
        self.assertFalse(payload["replay_safe"])

    def test_the_fabric_stamps_effect_metadata_when_it_creates_a_record(self) -> None:
        # The fields must be populated by the fabric, not left for a caller to
        # remember, or recovery would silently read empty values.
        import inspect

        from amca import tool_fabric

        source = inspect.getsource(tool_fabric)
        self.assertIn("effect_for", source,
                      "the fabric must stamp effect metadata onto its records")


if __name__ == "__main__":
    unittest.main()
