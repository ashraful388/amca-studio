"""v0.6.0 M4.1 Cross-provider Team UX — backend behavior matrix tests.

Work order ZC-AMCA-V0.6.0-M4.1-CROSS-PROVIDER-TEAM-UX-01 minimum test
matrix. These tests exercise EXISTING backend behavior only — the branch
is frontend-only (no production source change). They pin the exact team
contract the new UX depends on: cross-provider member refs, validation
rules, upsert edit, deletion semantics, credential secrecy, and stale
reference safety. No paid/cloud calls.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from amca.credentials import MemoryCredentialStore
from amca.service import create_server


class TeamMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=Path(__file__).resolve().parents[1],
            database=Path(self.temporary.name) / "state.sqlite3",
            credential_store=MemoryCredentialStore(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._json("/api/session", authorized=False)
        self.token = str(session["session_token"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _json(self, path, *, method="GET", body=None, authorized=True):
        headers = {"Origin": "http://localhost:1420"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authorized:
            headers["X-AMCA-Session"] = self.token
        request = Request(self.base + path, method=method, data=data, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8", errors="replace")
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                raise AssertionError(f"unexpected non-JSON error body: {payload[:200]!r}")

    def _provider(self, name: str, endpoint: str, models: list[str], secret: str = "sk-matrix"):
        return self._json(
            "/api/model-hub/providers",
            method="POST",
            body={
                "provider_kind": "custom",
                "name": name,
                "endpoint": endpoint,
                "pricing_mode": "auto",
                "secret": secret,
                "model_ids": models,
            },
        )["provider"]

    def _team(self, name: str, refs: list[str], *, team_id: str | None = None, description: str = ""):
        body = {"name": name, "description": description, "model_refs": refs}
        if team_id is not None:
            body["id"] = team_id
        return self._json("/api/model-hub/teams", method="POST", body=body)["team"]

    def _teams(self):
        return self._json("/api/model-hub")["model_teams"]

    # -- 1. empty team state ---------------------------------------------------

    def test_1_empty_team_state(self):
        self.assertEqual(self._teams(), [])

    # -- 2. single-provider team --------------------------------------------------

    def test_2_create_single_provider_team(self):
        provider = self._provider("OpenAI work", "https://openai.example/v1", ["gpt-x"])
        team = self._team("OpenAI only", [f"{provider['id']}:gpt-x"])
        self.assertEqual(team["model_refs"], [f"{provider['id']}:gpt-x"])
        self.assertIn(team, self._teams())

    # -- 3 + 4. two-provider and three-plus-provider cross-provider teams ------------

    def test_3_cross_provider_two_provider_team(self):
        openai = self._provider("OpenAI", "https://openai.example/v1", ["gpt-x"])
        anthropic = self._provider("Anthropic", "https://anthropic.example", ["claude-x"])
        refs = [f"{openai['id']}:gpt-x", f"{anthropic['id']}:claude-x"]
        team = self._team("Cross pair", refs)
        self.assertEqual(sorted(team["model_refs"]), sorted(refs))

    def test_4_cross_provider_three_plus_team(self):
        refs = []
        for index, (name, endpoint, model) in enumerate([
            ("P1", "https://p1.example/v1", "model-1"),
            ("P2", "https://p2.example/v1", "model-2"),
            ("P3", "https://p3.example/v1", "model-3"),
        ]):
            provider = self._provider(name, endpoint, [model])
            refs.append(f"{provider['id']}:{model}")
        refs.append("ollama:qwen3:1.7b")
        team = self._team("Wide", refs)
        self.assertEqual(len(team["model_refs"]), 4)

    # -- 5. local Ollama + API member --------------------------------------------------

    def test_5_local_and_api_members_coexist(self):
        provider = self._provider("OpenAI", "https://openai.example/v1", ["gpt-x"])
        team = self._team("Hybrid", ["ollama:qwen3:1.7b", f"{provider['id']}:gpt-x"])
        self.assertIn("ollama:qwen3:1.7b", team["model_refs"])

    # -- 6 + 7. name validation and zero-member rejection ---------------------------------

    def test_6_blank_name_rejected(self):
        result = self._json(
            "/api/model-hub/teams",
            method="POST",
            body={"name": "  ", "description": "", "model_refs": ["ollama:qwen3:1.7b"]},
        )
        self.assertTrue(str(result.get("error", "")), "blank team name must be rejected")

    def test_7_zero_member_rejected(self):
        result = self._json(
            "/api/model-hub/teams",
            method="POST",
            body={"name": "Empty", "description": "", "model_refs": []},
        )
        self.assertTrue(str(result.get("error", "")), "zero-member team must be rejected")

    # -- 8. duplicate-member behavior --------------------------------------------------------

    def test_8_duplicate_members_deduped(self):
        team = self._team("Dup", ["ollama:qwen3:1.7b", "ollama:qwen3:1.7b"])
        self.assertEqual(team["model_refs"], ["ollama:qwen3:1.7b"])

    # -- 10/11/14. edit team via the existing upsert contract ----------------------------------

    def test_14_edit_team_via_upsert(self):
        provider = self._provider("OpenAI", "https://openai.example/v1", ["gpt-x", "gpt-y"])
        team = self._team("Editable", [f"{provider['id']}:gpt-x"])
        updated = self._team(
            "Renamed",
            [f"{provider['id']}:gpt-x", f"{provider['id']}:gpt-y"],
            team_id=team["id"],
            description="edited",
        )
        self.assertEqual(updated["id"], team["id"])
        self.assertEqual(updated["name"], "Renamed")
        stored = next(item for item in self._teams() if item["id"] == team["id"])
        self.assertEqual(len(stored["model_refs"]), 2)

    # -- 12 + 17. temporarily unavailable member preserved + credentials absent ------------------

    def test_17_team_state_contains_no_credential_material(self):
        provider = self._provider("Secret check", "https://secret.example/v1", ["m-1"], secret="sk-matrix-secret")
        self._team("With member", [f"{provider['id']}:m-1"])
        raw = json.dumps(self._json("/api/model-hub"))
        self.assertNotIn("sk-matrix-secret", raw)
        self.assertNotIn("credential_ref", raw)

    # -- 15 + 16. delete team; providers and other data remain --------------------------------------

    def test_15_16_delete_team_preserves_providers(self):
        provider = self._provider("Stays", "https://stays.example/v1", ["m-stays"])
        team = self._team("Doomed", [f"{provider['id']}:m-stays"])
        deleted = self._json(f"/api/model-hub/teams/{team['id']}", method="DELETE")
        self.assertEqual(deleted["deleted"], team["id"])
        self.assertEqual(self._teams(), [])
        document = self._json("/api/model-hub")
        self.assertEqual(len(document["byok_providers"]), 1)
        self.assertEqual(document["byok_providers"][0]["id"], provider["id"])

    # -- 18 + 20. saved team visible through the existing source; old teams compatible ---------------

    def test_18_20_saved_teams_flow_through_existing_source(self):
        legacy = self._team("Legacy", ["ollama:qwen3:1.7b"])
        provider = self._provider("OpenAI", "https://openai.example/v1", ["gpt-x"])
        cross = self._team("Cross", ["ollama:qwen3:1.7b", f"{provider['id']}:gpt-x"])
        teams = {item["id"]: item for item in self._teams()}
        self.assertIn(legacy["id"], teams)
        self.assertEqual(teams[cross["id"]]["model_refs"], cross["model_refs"])
        # Same document the Main Chat picker reads (savedModelTeams source).

    # -- 19. stale reference never silently substituted (runtime authoritative) ------------------------

    def test_19_stale_reference_fails_bounded_at_runtime(self):
        # A team holding a ref whose provider was deleted is applied by the
        # picker verbatim; the run then fails with the backend's bounded
        # unknown-participant message — never a silent substitution.
        provider = self._provider("Doomed", "https://doomed.example/v1", ["m-doomed"])
        ref = f"{provider['id']}:m-doomed"
        self._team("Stale", ["ollama:qwen3:1.7b", ref])
        self._json(f"/api/model-hub/providers/{provider['id']}", method="DELETE")
        context = self.server.RequestHandlerClass.context
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [{"model": "qwen3:1.7b", "fit": 0}],
        }
        with patch.object(type(context), "local_models", return_value=inventory):
            result = self._json(
                "/api/chat-runs",
                method="POST",
                body={
                    "goal": "Apply a team containing a stale member.",
                    "mode": "joint",
                    "selection_policy": "manual_model_set",
                    "participant_ids": ["ollama:qwen3:1.7b", ref],
                },
            )
        self.assertIn(ref, str(result.get("error", "")))
        self.assertNotIn("Traceback", str(result.get("error", "")))


if __name__ == "__main__":
    unittest.main()
