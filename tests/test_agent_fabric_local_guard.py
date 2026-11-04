"""Gate-2 follow-up (independent-review finding 1, MEDIUM): Agent-Fabric
execution is local-model-only — a cloud participant must be refused with a
fail-closed PermissionError instead of transmitting the goal text without
an approval card. The guard is unconditional (no request seam can bypass
it); local participants proceed."""

from __future__ import annotations

import gc
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.models import ScriptedAdapter
from amca.service import ServiceContext

SOFTWARE = Path(__file__).resolve().parents[1]


def _cloud_adapter() -> ScriptedAdapter:
    # ScriptedAdapter defaults to is_local=False: a non-local (cloud) adapter.
    return ScriptedAdapter(
        scripts=["cloud reply"],
        provider="custom",
        model="cloud-x",
    )


class AgentFabricLocalOnlyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.context = ServiceContext(
            root=SOFTWARE,
            database=Path(self.temp_dir.name) / "amca.db",
            benchmarks=SOFTWARE / "benchmarks" / "tasks.json",
        )

    def tearDown(self) -> None:
        # Windows: a sqlite connection finalizing during rmtree holds the
        # database file briefly; collect first, then tolerate the race.
        gc.collect()
        shutil.rmtree(self.temp_dir.name, ignore_errors=True)

    def test_plan_execute_refuses_cloud_participant(self) -> None:
        with self.assertRaises(PermissionError) as caught:
            self.context.agent_plan_execute({
                "goal": "demo goal",
                "participant": "cloud-x",
                "_test_adapter": _cloud_adapter(),
            })
        self.assertIn("local-model-only", str(caught.exception))

    def test_tool_start_refuses_cloud_participant(self) -> None:
        # Fail-closed refusal: this path's tool-support guard fires before the
        # local-only guard for an unknown cloud model, but the participant is
        # refused either way — no execution without a governed local model.
        with self.assertRaises((PermissionError, ValueError)):
            self.context.agent_tool_start({
                "goal": "demo goal",
                "participant": "cloud-x",
                "tools": ["filesystem.read"],
                "_test_adapter": _cloud_adapter(),
            })

    def test_local_participant_is_not_blocked_by_the_guard(self) -> None:
        # A local adapter must pass the guard; any later failure is plan or
        # fixture machinery, never the local-only guard itself.
        local = ScriptedAdapter(
            scripts=["local reply"],
            provider="ollama",
            model="qwen3:1.7b",
            is_local=True,
        )
        raised_guard = False
        try:
            self.context.agent_plan_execute({
                "goal": "demo goal",
                "participant": "local-x",
                "_test_adapter": local,
            })
        except PermissionError as caught:
            if "local-model-only" in str(caught):
                raised_guard = True
        except Exception:
            pass
        self.assertFalse(raised_guard, "the guard must not refuse a local adapter")


if __name__ == "__main__":
    unittest.main()
