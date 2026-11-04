"""PS1-13C (fix PS1-13B-SEC-01): govern agent-originated artifact ops.

Covers the pre-fix repro shape (spoofed ``created_by`` with no capability
grant), authorized/unauthorized/spoofed/cross-scope paths, invalid-agent and
grant-replay refusal, audit binding, unchanged top-level flows, and focused
governor/router/store contract assertions.

All mutable test state lives under the mandated scratch root
``D:\\ChatGPT\\AMCA\\.agent-tmp\\ps1-agent-13c`` (work-order constraint).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from amca.agent_fabric import AgentToolRouter
from amca.service import ServiceContext

MANDATED_SCRATCH = Path(r"D:\ChatGPT\AMCA\.agent-tmp\ps1-agent-13c")
PROJECT_A = "ps1-13c-proj-a"
PROJECT_B = "ps1-13c-proj-b"
ARTIFACT_TOOLS = (
    "artifact.author",
    "artifact.edit",
    "artifact.restore",
    "artifact.export",
)

# Grant-replay note: artifact governance is capability-based (assigned tool
# capabilities via AgentToolRouter), not LocalPermissionStore grant-based, so
# a replayed/fabricated ``grant_id`` must neither authorize nor bypass the
# capability check. There is no grant contract here -> NOT_APPLICABLE for a
# grant-replay accept path; the capability decision still refuses.
REPLAY_NOT_APPLICABLE_REASON = (
    "artifact ops are capability-governed, not grant-governed; "
    "fabricated grant material is ignored and the capability decision refuses"
)


def _agent_values(name: str, tools: list[str]) -> dict[str, object]:
    return {
        "name": name,
        "description": "PS1-13C governance probe agent.",
        "instructions": "Obey the deterministic tool router.",
        "category": "Custom",
        "allowed_tools": tools,
        "permissions": [],
        "can_spawn": False,
    }


class AgentArtifactGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        root = Path(__file__).resolve().parents[1]
        self.context = ServiceContext(
            root=root,
            database=Path(self.temporary.name) / "state.sqlite3",
            benchmarks=root / "benchmarks" / "tasks.json",
        )
        governed = self.context.agent_fabric_store.save_custom_agent(
            _agent_values("PS1-13C Governed", list(ARTIFACT_TOOLS))
        )
        restricted = self.context.agent_fabric_store.save_custom_agent(
            _agent_values("PS1-13C Restricted", ["filesystem.read"])
        )
        self.governed_id = str(governed["id"])
        self.restricted_id = str(restricted["id"])
        self.governed_actor = f"agent:{self.governed_id}"

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    # -- helpers ---------------------------------------------------------
    def _author_top_level(self, project: str, title: str) -> dict[str, object]:
        return self.context.artifact_author(
            project,
            {
                "artifact_type": "markdown",
                "title": title,
                "content": "# seeded\nbody",
                "created_by": "desktop_user",
            },
        )

    # -- SEC-01 pre-fix repro shape --------------------------------------
    def test_sec01_pre_fix_repro_spoof_without_capability_denied(self) -> None:
        """Spoofed created_by + agent_id with NO grant must be denied."""
        with self.assertRaises(PermissionError):
            self.context.artifact_author(
                PROJECT_A,
                {
                    "artifact_type": "markdown",
                    "title": "SEC-01 repro spoof",
                    "content": "# spoofed\nbody",
                    "created_by": "desktop_user",
                    "agent_id": self.restricted_id,
                },
            )

    # -- authorized -------------------------------------------------------
    def test_authorized_agent_author_binds_actor_identity(self) -> None:
        created = self.context.artifact_author(
            PROJECT_A,
            {
                "artifact_type": "markdown",
                "title": "Governed author",
                "content": "# governed\nbody",
                "created_by": "desktop_user",
                "agent_id": self.governed_id,
            },
        )
        self.assertEqual(created["created_by"], self.governed_actor)
        self.assertEqual(created["project_id"], PROJECT_A)
        versions = created["versions"]
        self.assertEqual(versions[0]["created_by"], self.governed_actor)

    def test_authorized_agent_edit_restore_export_succeed(self) -> None:
        created = self.context.artifact_author(
            PROJECT_A,
            {
                "artifact_type": "markdown",
                "title": "Governed lifecycle",
                "content": "# v1\nbody",
                "created_by": "desktop_user",
                "agent_id": self.governed_id,
            },
        )
        aid = str(created["artifact_id"])
        edited = self.context.artifact_edit(
            PROJECT_A,
            aid,
            {
                "content": "# v2\nbody\nmore",
                "base_version": 1,
                "reason": "analyst update",
                "created_by": "desktop_user",
                "agent_id": self.governed_id,
            },
        )
        self.assertEqual(edited["current_version"], 2)
        restored = self.context.artifact_restore(
            PROJECT_A,
            aid,
            {"version": 1, "created_by": "desktop_user", "agent_id": self.governed_id},
        )
        self.assertEqual(restored["current_version"], 3)
        exported = self.context.artifact_export(
            PROJECT_A, aid, {"format": "html", "agent_id": self.governed_id}
        )
        self.assertEqual(exported["status"], "exported")

    # -- unauthorized / spoofed -------------------------------------------
    def test_unauthorized_agent_edit_restore_export_denied(self) -> None:
        seeded = self._author_top_level(PROJECT_A, "Seeded for denial")
        aid = str(seeded["artifact_id"])
        with self.assertRaises(PermissionError):
            self.context.artifact_edit(
                PROJECT_A,
                aid,
                {
                    "content": "# hostile\nbody",
                    "base_version": 1,
                    "agent_id": self.restricted_id,
                },
            )
        with self.assertRaises(PermissionError):
            self.context.artifact_restore(
                PROJECT_A, aid, {"version": 1, "agent_id": self.restricted_id}
            )
        with self.assertRaises(PermissionError):
            self.context.artifact_export(
                PROJECT_A, aid, {"format": "html", "agent_id": self.restricted_id}
            )
        # Denied mutations must not advance the version.
        self.assertEqual(self.context.artifact_meta(PROJECT_A, aid)["current_version"], 1)

    def test_spoofed_created_by_ignored_for_governed_edit(self) -> None:
        created = self.context.artifact_author(
            PROJECT_A,
            {
                "artifact_type": "markdown",
                "title": "Spoof target",
                "content": "# v1\nbody",
                "created_by": "desktop_user",
                "agent_id": self.governed_id,
            },
        )
        aid = str(created["artifact_id"])
        edited = self.context.artifact_edit(
            PROJECT_A,
            aid,
            {
                "content": "# v2\nspoof attempt",
                "base_version": 1,
                "reason": "spoof attempt",
                "created_by": "desktop_user",
                "agent_id": self.governed_id,
            },
        )
        latest = edited["versions"][-1]
        self.assertEqual(latest["created_by"], self.governed_actor)
        provenance = json.loads(str(latest["provenance"]))
        self.assertIn(self.governed_id, str(provenance.get("reason", "")))

    # -- cross-scope --------------------------------------------------------
    def test_cross_scope_reads_denied(self) -> None:
        seeded = self._author_top_level(PROJECT_A, "Scoped read guard")
        aid = str(seeded["artifact_id"])
        with self.assertRaises(PermissionError):
            self.context.artifact_meta(PROJECT_B, aid)
        with self.assertRaises(PermissionError):
            self.context.artifact_source(PROJECT_B, aid, None)

    def test_cross_scope_mutations_denied_top_level_and_agent(self) -> None:
        seeded = self._author_top_level(PROJECT_A, "Scoped mutation guard")
        aid = str(seeded["artifact_id"])
        with self.assertRaises(PermissionError):
            self.context.artifact_edit(
                PROJECT_B, aid, {"content": "# x\nbody", "base_version": 1}
            )
        with self.assertRaises(PermissionError):
            self.context.artifact_restore(PROJECT_B, aid, {"version": 1})
        with self.assertRaises(PermissionError):
            self.context.artifact_export(PROJECT_B, aid, {"format": "html"})
        with self.assertRaises(PermissionError):
            self.context.artifact_edit(
                PROJECT_B,
                aid,
                {
                    "content": "# x\nbody",
                    "base_version": 1,
                    "agent_id": self.governed_id,
                },
            )
        with self.assertRaises(PermissionError):
            self.context.artifact_export(
                PROJECT_B, aid, {"format": "html", "agent_id": self.governed_id}
            )
        self.assertEqual(self.context.artifact_meta(PROJECT_A, aid)["current_version"], 1)

    # -- replay / invalid grant ----------------------------------------------
    def test_invalid_or_unknown_agent_refused(self) -> None:
        with self.assertRaises(PermissionError):
            self.context.artifact_author(
                PROJECT_A,
                {
                    "artifact_type": "markdown",
                    "title": "Unknown agent",
                    "content": "# x\nbody",
                    "agent_id": "no-such-agent-ps1-13c",
                },
            )
        seeded = self._author_top_level(PROJECT_A, "Unknown agent edit")
        aid = str(seeded["artifact_id"])
        with self.assertRaises(PermissionError):
            self.context.artifact_edit(
                PROJECT_A,
                aid,
                {"content": "# x\nbody", "base_version": 1, "agent_id": "no-such-agent-ps1-13c"},
            )
        with self.assertRaises(PermissionError):
            self.context.artifact_export(
                PROJECT_A, aid, {"format": "html", "agent_id": "no-such-agent-ps1-13c"}
            )

    def test_fabricated_grant_material_neither_authorizes_nor_bypasses(self) -> None:
        """Replayed grant ids are NOT_APPLICABLE: capability decision stands."""
        with self.assertRaises(PermissionError):
            self.context.artifact_author(
                PROJECT_A,
                {
                    "artifact_type": "markdown",
                    "title": "Replayed grant",
                    "content": "# x\nbody",
                    "agent_id": self.restricted_id,
                    "grant_id": "replayed-grant-ps1-13c",
                    "approval_id": "replayed-approval-ps1-13c",
                },
            )
        created = self.context.artifact_author(
            PROJECT_A,
            {
                "artifact_type": "markdown",
                "title": "Grant noise ignored",
                "content": "# x\nbody",
                "agent_id": self.governed_id,
                "grant_id": "replayed-grant-ps1-13c",
            },
        )
        self.assertEqual(created["created_by"], self.governed_actor)
        self.assertTrue(REPLAY_NOT_APPLICABLE_REASON)

    # -- normal top-level flows unchanged --------------------------------------
    def test_normal_top_level_flows_unchanged(self) -> None:
        created = self.context.artifact_author(
            PROJECT_A,
            {
                "artifact_type": "markdown",
                "title": "Quarterly report",
                "content": "# Q3\nbody",
                "created_by": "model:test via chat c9",
                "chat_id": "c9",
            },
        )
        aid = str(created["artifact_id"])
        self.assertEqual(created["current_version"], 1)
        self.assertEqual(created["created_by"], "model:test via chat c9")

        edited = self.context.artifact_edit(
            PROJECT_A, aid, {"content": "# Q3\nbody\nmore", "base_version": 1}
        )
        self.assertEqual(edited["current_version"], 2)

        restored = self.context.artifact_restore(PROJECT_A, aid, {"version": 1})
        self.assertEqual(restored["current_version"], 3)

        source = self.context.artifact_source(PROJECT_A, aid, 2)
        self.assertIn("more", str(source["content"]))

        meta = self.context.artifact_meta(PROJECT_A, aid)
        self.assertEqual(meta["artifact_id"], aid)

        listed = self.context.artifact_list(PROJECT_A)
        self.assertEqual(listed[0]["artifact_id"], aid)

        exported = self.context.artifact_export(PROJECT_A, aid, {"format": "html"})
        self.assertEqual(exported["status"], "exported")
        self.assertEqual(exported["target_format"], "html")

    # -- focused governor / router / store contract ------------------------------
    def test_governor_router_store_contract(self) -> None:
        capabilities = self.context.agent_fabric_store.tool_capabilities(self.governed_id)
        self.assertIn("artifact.author", capabilities)
        allowed = AgentToolRouter.authorize(self.governed_id, "artifact.edit", capabilities)
        self.assertTrue(allowed["authorized"])
        self.assertEqual(allowed["agent_id"], self.governed_id)
        self.assertEqual(allowed["tool_id"], "artifact.edit")
        self.assertIn("governor", allowed)
        denied = AgentToolRouter.authorize(
            self.restricted_id,
            "artifact.author",
            self.context.agent_fabric_store.tool_capabilities(self.restricted_id),
        )
        self.assertFalse(denied["authorized"])
        seeded = self._author_top_level(PROJECT_A, "Store binding probe")
        self.assertEqual(seeded["project_id"], PROJECT_A)


if __name__ == "__main__":
    unittest.main()
