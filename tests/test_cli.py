from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from amca.credentials import MemoryCredentialStore, ProviderCredentialResolver

from amca.cli import _live_adapters, main
from amca.models import ScriptedAdapter
from amca.storage import SQLiteStore


class CLITests(unittest.TestCase):
    def test_credentials_use_hidden_windows_store_without_echoing_secret(self) -> None:
        store = MemoryCredentialStore()
        with patch("amca.cli.WindowsCredentialStore") as store_type:
            store_type.is_available.return_value = True
            store_type.return_value = store
            with patch("amca.cli.getpass.getpass", return_value="secret-canary"):
                set_stdout = io.StringIO()
                with contextlib.redirect_stdout(set_stdout):
                    set_code = main(
                        ["credentials", "set", "--provider", "openai"]
                    )
            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                status_code = main(
                    ["credentials", "status", "--provider", "openai"]
                )
            delete_stdout = io.StringIO()
            with contextlib.redirect_stdout(delete_stdout):
                delete_code = main(
                    ["credentials", "delete", "--provider", "openai"]
                )

        self.assertEqual((set_code, status_code, delete_code), (0, 0, 0))
        combined = set_stdout.getvalue() + status_stdout.getvalue() + delete_stdout.getvalue()
        self.assertNotIn("secret-canary", combined)
        self.assertTrue(json.loads(status_stdout.getvalue())["configured"]["openai"])
        self.assertTrue(json.loads(delete_stdout.getvalue())["deleted"])

    def test_live_adapter_factory_covers_cloud_and_named_local_backends(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pricing = Path(directory) / "pricing.json"
            pricing.write_text(
                json.dumps(
                    {
                        "models": {
                            name: {
                                "input_per_million_tokens": 1,
                                "output_per_million_tokens": 2,
                            }
                            for name in ("oa", "an", "ki", "ge")
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                provider=[
                    "openai=oa",
                    "anthropic=an",
                    "kimi=ki",
                    "gemini=ge",
                    "ollama=ol",
                    "llamacpp=lc",
                    "vllm=vl",
                    "sglang=sg",
                ],
                pricing=str(pricing),
                ollama_url="http://127.0.0.1:11434",
                llama_cpp_url="http://127.0.0.1:8080/v1",
                vllm_url="http://127.0.0.1:8000/v1",
                sglang_url="http://127.0.0.1:30000/v1",
            )
            with patch(
                "amca.cli._provider_credential_resolver",
                return_value=ProviderCredentialResolver(MemoryCredentialStore()),
            ):
                adapters = _live_adapters(args)

        self.assertEqual(
            [adapter.provider for adapter in adapters],
            ["openai", "anthropic", "kimi", "gemini", "ollama", "llama.cpp", "vllm", "sglang"],
        )
        self.assertEqual(
            [adapter.capabilities().is_local for adapter in adapters],
            [False, False, False, False, True, True, True, True],
        )

    def test_project_create_list_stop_and_resume_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "projects.sqlite3"
            create_stdout = io.StringIO()
            with contextlib.redirect_stdout(create_stdout):
                create_code = main(
                    [
                        "project",
                        "create",
                        "--project-id",
                        "cli-project",
                        "--goal",
                        "Produce a verified local result",
                        "--criterion-json",
                        json.dumps(
                            {
                                "id": "verified-result",
                                "description": "The result equals 42.",
                                "evaluator_kind": "exact_value",
                                "expected_value": 42,
                            }
                        ),
                        "--constraint",
                        "Do not transmit project data.",
                        "--max-api-cost",
                        "0",
                        "--database",
                        str(database),
                    ]
                )
            created = json.loads(create_stdout.getvalue())
            list_stdout = io.StringIO()
            with contextlib.redirect_stdout(list_stdout):
                list_code = main(
                    ["project", "list", "--database", str(database)]
                )
            listed = json.loads(list_stdout.getvalue())
            stop_stdout = io.StringIO()
            with contextlib.redirect_stdout(stop_stdout):
                stop_code = main(
                    [
                        "project",
                        "stop",
                        "--project-id",
                        "cli-project",
                        "--reason",
                        "Operator stopped the run",
                        "--database",
                        str(database),
                    ]
                )
            stopped = json.loads(stop_stdout.getvalue())
            resume_stdout = io.StringIO()
            with contextlib.redirect_stdout(resume_stdout):
                resume_code = main(
                    [
                        "project",
                        "resume",
                        "--project-id",
                        "cli-project",
                        "--dry-run",
                        "--database",
                        str(database),
                    ]
                )
            recovery = json.loads(resume_stdout.getvalue())

        self.assertEqual(create_code, 0)
        self.assertTrue(created["created"])
        self.assertEqual(created["project"]["status"], "created")
        self.assertEqual(created["project"]["budget"]["max_iterations"], 6)
        self.assertEqual(created["project"]["budget"]["max_api_cost_usd"], 0.0)
        self.assertEqual(list_code, 0)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["projects"][0]["project_id"], "cli-project")
        self.assertEqual(stop_code, 0)
        self.assertTrue(stopped["changed"])
        self.assertEqual(stopped["from_status"], "created")
        self.assertEqual(stopped["to_status"], "stopped")
        self.assertEqual(stopped["state_version"], 1)
        self.assertEqual(resume_code, 0)
        self.assertTrue(recovery["completed"])
        self.assertEqual(recovery["state_summary"]["status"], "stopped")

    def test_project_create_accepts_strict_contract_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "projects.sqlite3"
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "project_id": "file-contract",
                        "objective": "Check a bounded numerical result",
                        "success_criteria": [
                            {
                                "criterion_id": "numeric-check",
                                "description": "The result is within tolerance.",
                                "evaluator_kind": "numeric_tolerance",
                                "expected_value": 3.14,
                                "tolerance": 0.01,
                            }
                        ],
                        "constraints": ["Remain offline."],
                        "budget": {
                            "iteration_cap": 4,
                            "wall_clock_seconds": 30,
                        },
                        "privacy_mode": "offline",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "project",
                        "create",
                        "--contract",
                        str(contract),
                        "--database",
                        str(database),
                    ]
                )
            result = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(result["project"]["project_id"], "file-contract")
        self.assertEqual(result["goal_contract"]["max_iterations"], 4)
        self.assertEqual(result["goal_contract"]["max_runtime_seconds"], 30.0)
        self.assertEqual(result["constraints"], ["Remain offline."])

    def test_project_create_rejects_result_injection_without_writing(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "projects.sqlite3"
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "project",
                        "create",
                        "--project-id",
                        "unsafe-project",
                        "--goal",
                        "Unsafe attempted result",
                        "--criterion-json",
                        json.dumps(
                            {
                                "description": "Injected pass",
                                "status": "pass",
                            }
                        ),
                        "--database",
                        str(database),
                    ]
                )
            with contextlib.redirect_stdout(io.StringIO()):
                list_code = main(
                    ["project", "list", "--database", str(database)]
                )

        self.assertEqual(code, 2)
        self.assertEqual(list_code, 0)
        self.assertIn("unsupported fields: status", stderr.getvalue())

    def test_run_reuses_created_project_contract_without_replacing_version_zero(self) -> None:
        response = json.dumps(
            {
                "summary": "A bounded provisional response",
                "events": [],
                "criterion_evidence": [],
                "focus_resolved": True,
            }
        )
        adapter = ScriptedAdapter([response], adapter_id="local", is_local=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "projects.sqlite3"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "project",
                            "create",
                            "--project-id",
                            "created-run",
                            "--goal",
                            "Complete this persisted bounded goal",
                            "--criterion",
                            "A verified deliverable is recorded.",
                            "--constraint",
                            "Remain offline.",
                            "--compute-policy",
                            "balanced",
                            "--database",
                            str(database),
                        ]
                    ),
                    0,
                )
            output = root / "result.json"
            with patch("amca.cli._live_adapters", return_value=(adapter,)):
                with contextlib.redirect_stdout(io.StringIO()):
                    run_code = main(
                        [
                            "run",
                            "--goal",
                            "Complete this persisted bounded goal",
                            "--provider",
                            "ollama=local",
                            "--mode",
                            "solo",
                            "--sync",
                            "soft",
                            "--rounds",
                            "1",
                            "--compute-policy",
                            "balanced",
                            "--project-id",
                            "created-run",
                            "--database",
                            str(database),
                            "--output",
                            str(output),
                        ]
                    )
            result = json.loads(output.read_text(encoding="utf-8"))
            with SQLiteStore(database) as store:
                version_zero = store.load_state("created-run", 0)
                current = store.load_state("created-run")

        self.assertEqual(run_code, 2)
        self.assertEqual(adapter.calls_consumed, 1)
        self.assertEqual(version_zero.status, "created")  # type: ignore[union-attr]
        self.assertEqual(
            version_zero.goal_contract.criteria[0].description,  # type: ignore[union-attr]
            "A verified deliverable is recorded.",
        )
        self.assertEqual(current.user_constraints, ["Remain offline."])  # type: ignore[union-attr]
        self.assertGreater(current.version, 0)  # type: ignore[union-attr]
        self.assertEqual(
            result["state"]["goal_contract"]["criteria"][0]["description"],
            "A verified deliverable is recorded.",
        )

    def test_run_rejects_conflicting_created_project_before_provider_call(self) -> None:
        response = json.dumps(
            {
                "summary": "must not be consumed",
                "events": [],
                "criterion_evidence": [],
            }
        )
        adapter = ScriptedAdapter([response], adapter_id="local", is_local=True)
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "projects.sqlite3"
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "project",
                        "create",
                        "--project-id",
                        "goal-conflict",
                        "--goal",
                        "Persisted objective",
                        "--criterion",
                        "Persisted criterion",
                        "--compute-policy",
                        "balanced",
                        "--database",
                        str(database),
                    ]
                )
            with patch("amca.cli._live_adapters", return_value=(adapter,)):
                with contextlib.redirect_stderr(stderr):
                    code = main(
                        [
                            "run",
                            "--goal",
                            "Conflicting objective",
                            "--provider",
                            "ollama=local",
                            "--compute-policy",
                            "balanced",
                            "--project-id",
                            "goal-conflict",
                            "--database",
                            str(database),
                        ]
                    )
            with SQLiteStore(database) as store:
                state = store.load_state("goal-conflict")

        self.assertEqual(code, 2)
        self.assertEqual(adapter.calls_consumed, 0)
        self.assertIn("--goal conflicts", stderr.getvalue())
        self.assertEqual(state.status, "created")  # type: ignore[union-attr]
        self.assertEqual(state.version, 0)  # type: ignore[union-attr]

    def test_default_result_path_rejects_project_id_path_traversal(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["demo", "--project-id", "../outside"])

        self.assertEqual(code, 2)
        self.assertIn("cannot contain path separators", stderr.getvalue())

    def test_offline_demo_routes_joint_and_writes_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "demo",
                        "--project-id",
                        "cli-demo",
                        "--database",
                        str(root / "state.sqlite3"),
                        "--output",
                        str(output),
                    ]
                )
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(report["routing"]["collaboration_mode"], "joint")
        self.assertEqual(report["routing"]["sync_mode"], "hard")
        self.assertEqual(report["stop_reason"], "goal_satisfied")
        self.assertEqual(len(report["calls"]), 6)

    def test_offline_mode_rejects_cloud_before_call(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "run",
                        "--goal",
                        "A routine task",
                        "--provider",
                        "openai=placeholder-model",
                        "--privacy",
                        "offline",
                        "--database",
                        str(Path(directory) / "state.sqlite3"),
                    ]
                )

        self.assertEqual(code, 2)
        self.assertIn("Offline mode", stderr.getvalue())

    def test_private_hybrid_requires_reviewed_excerpt(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "run",
                    "--goal",
                    "Review privately",
                    "--provider",
                    "openai=placeholder-model",
                    "--privacy",
                    "private_hybrid",
                    "--project-id",
                    "private-review",
                    "--max-api-cost",
                    "1.00",
                ]
            )

        self.assertEqual(code, 2)
        self.assertIn("--private-excerpt", stderr.getvalue())

    def test_private_hybrid_creates_network_free_approval_then_cli_approves(self) -> None:
        excerpt_text = "reviewed bounded excerpt canary"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite3"
            excerpt = root / "excerpt.txt"
            excerpt.write_text(excerpt_text, encoding="utf-8")
            delegate = ScriptedAdapter(
                ["unused"],
                adapter_id="cloud-reviewer",
                provider="example-cloud",
                model="review-model",
            )
            stdout = io.StringIO()
            with patch("amca.cli._live_adapters", return_value=(delegate,)):
                with contextlib.redirect_stdout(stdout):
                    code = main(
                        [
                            "run",
                            "--goal",
                            "Review one excerpt",
                            "--provider",
                            "openai=review-model",
                            "--privacy",
                            "private_hybrid",
                            "--project-id",
                            "private-review",
                            "--private-excerpt",
                            str(excerpt),
                            "--max-api-cost",
                            "1.00",
                            "--database",
                            str(database),
                        ]
                    )
            waiting = json.loads(stdout.getvalue())
            approval_id = waiting["approval_requests"][0]["id"]
            approve_stdout = io.StringIO()
            with contextlib.redirect_stdout(approve_stdout):
                approve_code = main(
                    [
                        "approvals",
                        "approve",
                        "--id",
                        approval_id,
                        "--database",
                        str(database),
                    ]
                )
            approved = json.loads(approve_stdout.getvalue())
            with SQLiteStore(database) as store:
                waiting_state = store.load_state("private-review")

        self.assertEqual(code, 3)
        self.assertFalse(waiting["network_started"])
        self.assertEqual(waiting_state.status, "waiting_for_approval")  # type: ignore[union-attr]
        self.assertEqual(waiting_state.version, 1)  # type: ignore[union-attr]
        self.assertEqual(delegate.calls_consumed, 0)
        self.assertNotIn(excerpt_text, stdout.getvalue())
        self.assertEqual(approve_code, 0)
        self.assertEqual(approved["status"], "approved")

    def test_auto_novelty_falls_back_to_executable_joint_mode(self) -> None:
        response = json.dumps(
            {
                "summary": "provisional",
                "events": [],
                "criterion_evidence": [],
                "focus_resolved": True,
            }
        )
        adapters = (
            ScriptedAdapter([response], adapter_id="cloud-a"),
            ScriptedAdapter([response], adapter_id="cloud-b"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.json"
            with patch("amca.cli._live_adapters", return_value=adapters):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main(
                        [
                            "run",
                            "--goal",
                            "Explore a novel research hypothesis",
                            "--provider",
                            "openai=a",
                            "--provider",
                            "anthropic=b",
                            "--privacy",
                            "full_frontier",
                            "--max-api-cost",
                            "1.00",
                            "--novelty",
                            "--rounds",
                            "1",
                            "--database",
                            str(root / "state.sqlite3"),
                            "--output",
                            str(output),
                        ]
                    )
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 2)
        self.assertEqual(report["routing"]["collaboration_mode"], "joint")
        self.assertTrue(any("fell back" in reason for reason in report["routing"]["reasons"]))

    def test_unpriced_cloud_execution_is_disabled(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "run",
                        "--goal",
                        "A routine task",
                        "--provider",
                        "openai=unknown-model",
                        "--privacy",
                        "full_frontier",
                        "--max-api-cost",
                        "1.00",
                        "--database",
                        str(Path(directory) / "state.sqlite3"),
                    ]
                )

        self.assertEqual(code, 2)
        self.assertIn("unpriced paid calls are disabled", stderr.getvalue())

    def test_nonfinite_cloud_budget_is_rejected(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "run",
                    "--goal",
                    "A routine task",
                    "--provider",
                    "openai=placeholder-model",
                    "--privacy",
                    "full_frontier",
                    "--max-api-cost",
                    "nan",
                ]
            )

        self.assertEqual(code, 2)
        self.assertIn("finite and non-negative", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
