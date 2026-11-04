from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest

from amca.benchmarks import load_benchmark_suite
from amca.models import ModelRequest, ScriptedAdapter
from amca.r3_experiment import (
    _BoundedKimiAdapter,
    R3ManifestError,
    conservative_cost,
    load_manifest,
    run_experiment,
    validate_manifest,
)


MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "r3-first-heterogeneous.json"
)
V2_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v2.json")
V3_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v3.json")
V4_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v4.json")
V5_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v5.json")
V6_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v6.json")
V7_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v7.json")
V8_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v8.json")
V9_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v9.json")
V10_MANIFEST = MANIFEST.with_name("r3-first-heterogeneous-v10.json")


class PromptRecordingAdapter(ScriptedAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts: list[str] = []
        self.max_output_tokens: list[int | None] = []

    async def stream(self, request):
        self.prompts.append(str(request.messages[0]["content"]))
        self.max_output_tokens.append(request.max_output_tokens)
        async for chunk in super().stream(request):
            yield chunk


def _response(*, provisional: bool, evidence_only: bool = False) -> str:
    if provisional:
        evidence = []
        events = [
            {
                "type": "hypothesis",
                "content": "Set up suffix-prefix states before solving.",
                "severity": "info",
                "confidence": 0.8,
                "evidence_refs": [],
            }
        ]
    else:
        evidence_values = {
            "exact_expectation": {
                "exact_fraction": "33/4",
                "decimal": 8.25,
            },
            "transition_automaton": {
                "empty": {"H": "H", "T": "empty"},
                "H": {"H": "H", "T": "HT"},
                "HT": {"H": "done", "T": "empty"},
            },
            "first_step_equations": {
                "E_empty": {
                    "constant": "1",
                    "E_empty": "1/3",
                    "E_H": "2/3",
                },
                "E_H": {
                    "constant": "1",
                    "E_H": "2/3",
                    "E_HT": "1/3",
                },
                "E_HT": {"constant": "1", "E_empty": "1/3"},
            },
            "deterministic_verification": {
                "values": {
                    "E_empty": "33/4",
                    "E_H": "27/4",
                    "E_HT": "15/4",
                },
                "residuals": {"empty": "0", "H": "0", "HT": "0"},
            },
        }
        evidence = [
            {
                "criterion_id": criterion_id,
                "evidence": json.dumps(value, separators=(",", ":")),
            }
            for criterion_id, value in evidence_values.items()
        ]
        events = [] if evidence_only else [
            {
                "type": "correction",
                "content": "Retained the H-to-H self-overlap in the equations.",
                "severity": "medium",
                "confidence": 1.0,
                "evidence_refs": [],
            },
            {
                "type": "tool_request",
                "content": "Verify the exact decimal value.",
                "severity": "info",
                "confidence": 1.0,
                "evidence_refs": [],
                "tool": "math",
                "operation": "evaluate",
                "expression": "33/4",
                "criterion_id": "exact_expectation",
            },
        ]
    return json.dumps(
        {
            "summary": "provisional" if provisional else "Verified E_empty = 33/4.",
            "events": events,
            "criterion_evidence": evidence,
            "focus_resolved": not provisional,
        }
    )


def _scripted_participant(
    participant: str, scripts: int, *, evidence_only: bool = False
) -> PromptRecordingAdapter:
    sequence = []
    for _ in range(scripts // 2):
        sequence.extend(
            (
                _response(provisional=True),
                _response(provisional=False, evidence_only=evidence_only),
            )
        )
    return PromptRecordingAdapter(
        sequence,
        adapter_id=f"r3:{participant}",
        provider=participant,
        model=f"fake-{participant}",
    )


def _v8_response(
    *,
    provisional: bool,
    review_events: list[dict[str, object]] | None = None,
    focus_resolved: bool | None = None,
) -> str:
    if provisional:
        return json.dumps(
            {
                "summary": "Provisional E_empty = 39/4; check H self-overlap.",
                "events": [
                    {
                        "type": "hypothesis",
                        "content": (
                            "Provisional E_empty=39/4 using H--H-->H; "
                            "verify the first-step equations."
                        ),
                        "severity": "info",
                        "confidence": 0.6,
                        "evidence_refs": [],
                    }
                ],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )
    document = json.loads(_response(provisional=False, evidence_only=True))
    document["events"] = list(review_events or [])
    if focus_resolved is not None:
        document["focus_resolved"] = focus_resolved
    return json.dumps(document)


def _scripted_v8_participant(
    participant: str, scripts: int
) -> PromptRecordingAdapter:
    event_type = "objection" if participant == "anthropic" else "correction"
    review_event = {
        "type": event_type,
        "content": (
            "Object to 39/4; solving all three equations gives 33/4."
            if event_type == "objection"
            else "Correct provisional 39/4 to 33/4 after solving all three equations."
        ),
        "severity": "medium",
        "confidence": 1.0,
        "evidence_refs": [],
    }
    sequence = []
    for _ in range(scripts // 2):
        sequence.extend(
            (
                _v8_response(provisional=True),
                _v8_response(provisional=False, review_events=[review_event]),
            )
        )
    return PromptRecordingAdapter(
        sequence,
        adapter_id=f"r3:{participant}",
        provider=participant,
        model=f"fake-{participant}",
    )


def _tool_only_review() -> str:
    """Reproduce the valid-JSON/no-attestations shape observed in live v4 Solo."""

    return json.dumps(
        {
            "summary": "Provisional: derive and solve the state equations.",
            "events": [
                {
                    "type": "tool_request",
                    "content": "Solve the first-step linear system.",
                    "severity": "info",
                    "confidence": 0.9,
                    "evidence_refs": [],
                    "tool": "math",
                    "operation": "evaluate",
                    "expression": "Solve[{E0 == 1 + EH}, {E0, EH}]",
                }
            ],
            "criterion_evidence": [],
            "focus_resolved": False,
        }
    )


class R3ExperimentManifestTests(unittest.TestCase):
    def test_r3_kimi_requests_native_json_mode(self):
        adapter = _BoundedKimiAdapter(
            model="kimi-k2.6",
            api_key="test-only",
            json_mode=True,
            urlopen_fn=lambda *_args, **_kwargs: None,
        )
        payload = adapter._payload(
            ModelRequest.from_prompt("return JSON", max_output_tokens=512),
            stream=True,
        )
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["temperature"], 0.6)
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertEqual(payload["response_format"], {"type": "json_object"})

        legacy = _BoundedKimiAdapter(
            model="kimi-k2.6",
            api_key="test-only",
            urlopen_fn=lambda *_args, **_kwargs: None,
        )._payload(ModelRequest.from_prompt("return JSON"), stream=False)
        self.assertNotIn("response_format", legacy)

    def test_preregistered_manifest_is_bounded_and_reproducible(self):
        document = load_manifest(MANIFEST)
        bounds = document["execution_bounds"]
        self.assertEqual(bounds["maximum_total_calls"], 8)
        self.assertEqual(bounds["automatic_retries"], 0)
        self.assertEqual(bounds["rebase_calls"], 0)
        self.assertAlmostEqual(conservative_cost(document), 0.0280848)
        self.assertLessEqual(
            conservative_cost(document), bounds["maximum_total_api_cost_usd"]
        )

    def test_manifest_fails_closed_when_call_count_changes(self):
        document = copy.deepcopy(load_manifest(MANIFEST))
        document["execution_bounds"]["maximum_total_calls"] = 9
        with self.assertRaisesRegex(R3ManifestError, "maximum_total_calls"):
            validate_manifest(document)

    def test_v2_changes_only_the_preregistered_protocol_allowances(self):
        first = load_manifest(MANIFEST)
        second = load_manifest(V2_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "execution_bounds",
        }
        self.assertEqual(
            {key: value for key, value in first.items() if key not in ignored},
            {key: value for key, value in second.items() if key not in ignored},
        )
        bounds = second["execution_bounds"]
        self.assertEqual(second["supersedes"], first["experiment_id"])
        self.assertEqual(bounds["maximum_output_tokens_per_call"], 512)
        self.assertEqual(bounds["conservative_input_tokens_per_call"], 5000)
        self.assertAlmostEqual(conservative_cost(second), 0.0286264)
        self.assertLessEqual(
            conservative_cost(second), bounds["maximum_total_api_cost_usd"]
        )

    def test_v3_changes_only_the_staged_round_prompt_protocol(self):
        second = load_manifest(V2_MANIFEST)
        third = load_manifest(V3_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "round_prompt_protocol",
        }
        self.assertEqual(
            {key: value for key, value in second.items() if key not in ignored},
            {key: value for key, value in third.items() if key not in ignored},
        )
        self.assertEqual(third["supersedes"], second["experiment_id"])
        self.assertEqual(third["round_prompt_protocol"], "staged_compact_v1")

    def test_v4_changes_only_the_compact_review_prompt_version(self):
        third = load_manifest(V3_MANIFEST)
        fourth = load_manifest(V4_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "round_prompt_protocol",
        }
        self.assertEqual(
            {key: value for key, value in third.items() if key not in ignored},
            {key: value for key, value in fourth.items() if key not in ignored},
        )
        self.assertEqual(fourth["supersedes"], third["experiment_id"])
        self.assertEqual(fourth["round_prompt_protocol"], "staged_compact_v2")

    def test_v5_changes_only_the_evidence_only_prompt_version(self):
        fourth = load_manifest(V4_MANIFEST)
        fifth = load_manifest(V5_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "round_prompt_protocol",
            "stop_conditions",
        }
        self.assertEqual(
            {key: value for key, value in fourth.items() if key not in ignored},
            {key: value for key, value in fifth.items() if key not in ignored},
        )
        self.assertEqual(fifth["supersedes"], fourth["experiment_id"])
        self.assertEqual(fifth["round_prompt_protocol"], "staged_compact_v3")
        bounds = fifth["execution_bounds"]
        self.assertEqual(bounds["maximum_total_calls"], 8)
        self.assertEqual(bounds["maximum_output_tokens_per_call"], 512)
        self.assertAlmostEqual(conservative_cost(fifth), 0.0286264)

    def test_v6_changes_only_the_versioned_kimi_request_contract(self):
        fifth = load_manifest(V5_MANIFEST)
        sixth = load_manifest(V6_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "provider_request_protocol",
        }
        self.assertEqual(
            {key: value for key, value in fifth.items() if key not in ignored},
            {key: value for key, value in sixth.items() if key not in ignored},
        )
        self.assertEqual(sixth["supersedes"], fifth["experiment_id"])
        self.assertEqual(
            sixth["provider_request_protocol"], "kimi_json_object_v1"
        )
        self.assertAlmostEqual(conservative_cost(sixth), 0.0286264)

    def test_v7_changes_only_the_preregistered_review_contract(self):
        sixth = load_manifest(V6_MANIFEST)
        seventh = load_manifest(V7_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "review_contract_protocol",
            "stop_conditions",
        }
        self.assertEqual(
            {key: value for key, value in sixth.items() if key not in ignored},
            {key: value for key, value in seventh.items() if key not in ignored},
        )
        self.assertEqual(seventh["supersedes"], sixth["experiment_id"])
        self.assertEqual(
            seventh["review_contract_protocol"],
            "complete_attestations_empty_events_v1",
        )
        bounds = seventh["execution_bounds"]
        self.assertEqual(bounds["maximum_total_calls"], 8)
        self.assertEqual(bounds["automatic_retries"], 0)
        self.assertEqual(bounds["rebase_calls"], 0)
        self.assertAlmostEqual(conservative_cost(seventh), 0.0286264)

        incompatible = copy.deepcopy(seventh)
        incompatible["provider_request_protocol"] = None
        incompatible["round_prompt_protocol"] = "staged_compact_v2"
        with self.assertRaisesRegex(
            R3ManifestError, "exact evidence-only contract"
        ):
            validate_manifest(incompatible)

    def test_v8_changes_only_the_bounded_correction_protocol(self):
        seventh = load_manifest(V7_MANIFEST)
        eighth = load_manifest(V8_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "round_prompt_protocol",
            "review_contract_protocol",
            "stop_conditions",
        }
        self.assertEqual(
            {key: value for key, value in seventh.items() if key not in ignored},
            {key: value for key, value in eighth.items() if key not in ignored},
        )
        self.assertEqual(eighth["supersedes"], seventh["experiment_id"])
        self.assertEqual(eighth["round_prompt_protocol"], "staged_compact_v4")
        self.assertEqual(
            eighth["review_contract_protocol"],
            "complete_attestations_bounded_correction_v1",
        )
        self.assertAlmostEqual(conservative_cost(eighth), 0.0286264)

        incompatible = copy.deepcopy(eighth)
        incompatible["round_prompt_protocol"] = "staged_compact_v3"
        with self.assertRaisesRegex(R3ManifestError, "bounded-correction"):
            validate_manifest(incompatible)

    def test_v9_changes_only_the_preregistered_response_budget(self):
        eighth = load_manifest(V8_MANIFEST)
        ninth = load_manifest(V9_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "conditions",
            "execution_bounds",
        }
        self.assertEqual(
            {key: value for key, value in eighth.items() if key not in ignored},
            {key: value for key, value in ninth.items() if key not in ignored},
        )
        self.assertEqual(ninth["supersedes"], eighth["experiment_id"])
        for old_condition, new_condition in zip(
            eighth["conditions"], ninth["conditions"], strict=True
        ):
            self.assertEqual(
                {
                    key: value
                    for key, value in old_condition.items()
                    if key != "api_budget_usd"
                },
                {
                    key: value
                    for key, value in new_condition.items()
                    if key != "api_budget_usd"
                },
            )
        self.assertEqual(
            [item["api_budget_usd"] for item in ninth["conditions"]],
            [0.0065, 0.0235],
        )
        bounds = ninth["execution_bounds"]
        self.assertEqual(bounds["maximum_total_calls"], 8)
        self.assertEqual(bounds["maximum_output_tokens_per_call"], 640)
        self.assertEqual(bounds["conservative_input_tokens_per_call"], 4000)
        self.assertEqual(bounds["automatic_retries"], 0)
        self.assertEqual(bounds["rebase_calls"], 0)
        self.assertAlmostEqual(conservative_cost(ninth), 0.029168)
        self.assertLessEqual(
            conservative_cost(ninth), bounds["maximum_total_api_cost_usd"]
        )

    def test_v9_offline_runner_completes_exact_call_plan(self):
        adapters = {
            "openai": _scripted_v8_participant("openai", 2),
            "anthropic": _scripted_v8_participant("anthropic", 2),
            "kimi": _scripted_v8_participant("kimi", 4),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V9_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["actual_calls"], 8)
        self.assertEqual(report["automatic_retries"], 0)
        self.assertEqual(report["rebase_calls"], 0)
        self.assertTrue(report["comparison_evaluable"])
        self.assertTrue(
            all(item["review_protocol_evaluable"] for item in report["conditions"])
        )
        self.assertTrue(
            all(item["review_protocol_compliant"] for item in report["conditions"])
        )
        self.assertTrue(
            all(
                set(adapter.max_output_tokens) == {640}
                for adapter in adapters.values()
            )
        )
        joint = report["conditions"][1]
        round_two = [call for call in joint["calls"] if call["round"] == 2]
        self.assertTrue(all(call["focus_resolved"] for call in round_two))
        self.assertTrue(
            any(call["staleness"] == "compatible_merge" for call in round_two)
        )
        self.assertEqual(joint["missing_review_attestations"], {})

    def test_v9_unresolved_review_with_complete_evidence_omits_attestations(self):
        adapters = {
            "openai": _scripted_v8_participant("openai", 2),
            "anthropic": _scripted_v8_participant("anthropic", 2),
            "kimi": PromptRecordingAdapter(
                [
                    _v8_response(provisional=True),
                    _v8_response(provisional=False, focus_resolved=False),
                ],
                adapter_id="r3:kimi",
                provider="kimi",
                model="fake-kimi",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V9_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "partial_protocol_failure")
        self.assertEqual(report["actual_calls"], 2)
        self.assertEqual(len(report["conditions"]), 1)
        solo = report["conditions"][0]
        self.assertTrue(solo["provider_execution_complete"])
        self.assertTrue(solo["review_protocol_evaluable"])
        self.assertFalse(solo["review_protocol_compliant"])
        self.assertEqual(
            solo["missing_review_attestations"],
            {
                "r3:kimi": [
                    "exact_expectation",
                    "transition_automaton",
                    "first_step_equations",
                    "deterministic_verification",
                ]
            },
        )
        self.assertFalse(solo["calls"][1]["focus_resolved"])
        self.assertEqual(len(adapters["openai"].prompts), 0)
        self.assertEqual(len(adapters["anthropic"].prompts), 0)

    def test_v10_changes_only_the_accepted_review_protocol(self):
        ninth = load_manifest(V9_MANIFEST)
        tenth = load_manifest(V10_MANIFEST)
        ignored = {
            "experiment_id",
            "supersedes",
            "change_reason",
            "round_prompt_protocol",
            "review_contract_protocol",
            "stop_conditions",
        }
        self.assertEqual(
            {key: value for key, value in ninth.items() if key not in ignored},
            {key: value for key, value in tenth.items() if key not in ignored},
        )
        self.assertEqual(tenth["supersedes"], ninth["experiment_id"])
        self.assertEqual(tenth["round_prompt_protocol"], "staged_compact_v5")
        self.assertEqual(
            tenth["review_contract_protocol"],
            "complete_attestations_bounded_correction_v2",
        )
        self.assertAlmostEqual(conservative_cost(tenth), 0.029168)

        incompatible = copy.deepcopy(tenth)
        incompatible["round_prompt_protocol"] = "staged_compact_v4"
        with self.assertRaisesRegex(R3ManifestError, "resolved bounded-correction"):
            validate_manifest(incompatible)

    def test_v10_offline_runner_completes_exact_call_plan(self):
        adapters = {
            "openai": _scripted_v8_participant("openai", 2),
            "anthropic": _scripted_v8_participant("anthropic", 2),
            "kimi": _scripted_v8_participant("kimi", 4),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V10_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["actual_calls"], 8)
        self.assertEqual(report["automatic_retries"], 0)
        self.assertEqual(report["rebase_calls"], 0)
        self.assertTrue(report["comparison_evaluable"])
        self.assertTrue(
            all(item["review_protocol_compliant"] for item in report["conditions"])
        )
        self.assertTrue(
            all(
                call["focus_resolved"]
                for item in report["conditions"]
                for call in item["calls"]
                if call["round"] == 2
            )
        )
        for adapter in adapters.values():
            self.assertEqual(set(adapter.max_output_tokens), {640})
            for index, prompt in enumerate(adapter.prompts):
                if index % 2:
                    self.assertIn("at most 200 characters", prompt)
                    self.assertIn("focus_resolved value must remain true", prompt)

    def test_v10_accepts_178_character_objection(self):
        objection = {
            "type": "objection",
            "content": "x" * 178,
            "severity": "medium",
            "confidence": 1.0,
            "evidence_refs": [],
        }
        adapters = {
            "openai": _scripted_v8_participant("openai", 2),
            "anthropic": _scripted_v8_participant("anthropic", 2),
            "kimi": PromptRecordingAdapter(
                [
                    _v8_response(provisional=True),
                    _v8_response(provisional=False, review_events=[objection]),
                    _v8_response(provisional=True),
                    _v8_response(provisional=False, review_events=[objection]),
                ],
                adapter_id="r3:kimi",
                provider="kimi",
                model="fake-kimi",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V10_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "completed")
        self.assertTrue(
            all(
                item["review_event_contract_violations"] == {}
                for item in report["conditions"]
            )
        )

    def test_v10_fails_closed_on_unresolved_or_overlong_review(self):
        overlong = {
            "type": "correction",
            "content": "x" * 201,
            "severity": "medium",
            "confidence": 1.0,
            "evidence_refs": [],
        }
        for response, expected_violation in (
            (
                _v8_response(provisional=False, focus_resolved=False),
                "focus_not_resolved",
            ),
            (
                _v8_response(provisional=False, review_events=[overlong]),
                "content_over_200_chars",
            ),
        ):
            with self.subTest(expected_violation=expected_violation):
                adapters = {
                    "openai": _scripted_v8_participant("openai", 2),
                    "anthropic": _scripted_v8_participant("anthropic", 2),
                    "kimi": PromptRecordingAdapter(
                        [_v8_response(provisional=True), response],
                        adapter_id="r3:kimi",
                        provider="kimi",
                        model="fake-kimi",
                    ),
                }
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    report = asyncio.run(
                        run_experiment(
                            manifest=V10_MANIFEST,
                            database=root / "run.sqlite3",
                            output=root / "report.json",
                            adapters=adapters,
                        )
                    )
                self.assertEqual(report["status"], "partial_protocol_failure")
                self.assertEqual(report["actual_calls"], 2)
                self.assertEqual(len(report["conditions"]), 1)
                violations = report["conditions"][0][
                    "review_event_contract_violations"
                ]
                self.assertIn(expected_violation, violations["r3:kimi"])
                self.assertEqual(len(adapters["openai"].prompts), 0)
                self.assertEqual(len(adapters["anthropic"].prompts), 0)

    def test_v8_offline_runner_allows_one_bounded_correction(self):
        adapters = {
            "openai": _scripted_v8_participant("openai", 2),
            "anthropic": _scripted_v8_participant("anthropic", 2),
            "kimi": _scripted_v8_participant("kimi", 4),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V8_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["comparison_evaluable"])
        self.assertEqual(report["actual_calls"], 8)
        self.assertEqual(report["rebase_calls"], 0)
        self.assertTrue(
            all(item["review_protocol_compliant"] for item in report["conditions"])
        )
        self.assertTrue(
            all(item["review_protocol_evaluable"] for item in report["conditions"])
        )
        self.assertEqual(
            [item["event_counts"]["correction"] for item in report["conditions"]],
            [1, 2],
        )
        self.assertEqual(
            [item["event_counts"]["objection"] for item in report["conditions"]],
            [0, 1],
        )
        self.assertTrue(
            all(
                item["review_event_contract_violations"] == {}
                for item in report["conditions"]
            )
        )
        for adapter in adapters.values():
            for index, prompt in enumerate(adapter.prompts):
                if index % 2 == 0:
                    self.assertIn("Mandatory falsifiable round-one", prompt)
                    self.assertIn("provisional", prompt)
                    self.assertIn("exact expectation", prompt)
                    self.assertNotIn("Mandatory bounded-correction review", prompt)
                else:
                    self.assertIn("Mandatory bounded-correction review", prompt)
                    self.assertIn("exactly one correction or objection", prompt)
                    self.assertIn("at most 160 characters", prompt)
                    self.assertNotIn("Mandatory falsifiable round-one", prompt)

    def test_v8_stops_before_joint_on_review_event_contract_violations(self):
        correction = {
            "type": "correction",
            "content": "Correct the provisional fraction.",
            "severity": "medium",
            "confidence": 1.0,
            "evidence_refs": [],
        }
        forbidden = {
            "type": "evidence",
            "content": "x" * 161,
            "severity": "high",
            "evidence_refs": ["unverified-claim"],
        }
        adapters = {
            "openai": _scripted_v8_participant("openai", 2),
            "anthropic": _scripted_v8_participant("anthropic", 2),
            "kimi": PromptRecordingAdapter(
                [
                    _v8_response(provisional=True),
                    _v8_response(
                        provisional=False,
                        review_events=[correction, forbidden],
                    ),
                ],
                adapter_id="r3:kimi",
                provider="kimi",
                model="fake-kimi",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V8_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "partial_protocol_failure")
        self.assertEqual(report["actual_calls"], 2)
        self.assertEqual(len(report["conditions"]), 1)
        solo = report["conditions"][0]
        self.assertTrue(solo["provider_execution_complete"])
        self.assertTrue(solo["review_protocol_evaluable"])
        self.assertFalse(solo["review_protocol_compliant"])
        self.assertEqual(solo["missing_review_attestations"], {})
        self.assertEqual(
            solo["unexpected_review_events"], {"r3:kimi": {"evidence": 1}}
        )
        self.assertEqual(
            solo["review_event_contract_violations"],
            {
                "r3:kimi": {
                    "confidence_missing": 1,
                    "content_over_160_chars": 1,
                    "evidence_refs_not_empty": 1,
                    "event_count_over_1": 2,
                    "severity_not_allowed": 1,
                }
            },
        )
        self.assertEqual(len(adapters["openai"].prompts), 0)
        self.assertEqual(len(adapters["anthropic"].prompts), 0)

    def test_v9_provider_failure_makes_review_contract_not_evaluable(self):
        adapters = {
            "openai": _scripted_v8_participant("openai", 2),
            "anthropic": PromptRecordingAdapter(
                [_v8_response(provisional=True), "not-json"],
                adapter_id="r3:anthropic",
                provider="anthropic",
                model="fake-anthropic",
            ),
            "kimi": _scripted_v8_participant("kimi", 4),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V9_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "partial_provider_failure")
        self.assertFalse(report["comparison_evaluable"])
        self.assertIsNone(report["comparison"])
        self.assertEqual(report["actual_calls"], 8)
        self.assertEqual(report["automatic_retries"], 0)
        self.assertEqual(report["rebase_calls"], 0)
        self.assertEqual(len(report["conditions"]), 2)
        joint = report["conditions"][1]
        self.assertFalse(joint["provider_execution_complete"])
        self.assertTrue(joint["review_protocol_required"])
        self.assertFalse(joint["review_protocol_evaluable"])
        self.assertIsNone(joint["review_protocol_compliant"])
        self.assertEqual(joint["missing_review_attestations"], {})
        self.assertEqual(joint["unexpected_review_events"], {})
        self.assertEqual(joint["review_event_contract_violations"], {})
        failed_calls = [call for call in joint["calls"] if call["status"] == "failed"]
        self.assertEqual(len(failed_calls), 1)
        self.assertEqual(
            failed_calls[0]["error_class"], "Response is not valid JSON"
        )

    def test_task_reference_is_frozen_to_the_existing_hard_fixture(self):
        document = load_manifest(MANIFEST)
        suite = load_benchmark_suite(
            Path(__file__).resolve().parents[1] / "benchmarks" / "tasks.json"
        )
        task = suite.task(document["task"]["benchmark_task_id"])
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.raw["difficulty"], "hard")
        self.assertEqual(task.raw["reference"]["exact_fraction"], "33/4")
        self.assertEqual(task.raw["reference"]["expected_tosses"], 8.25)

    def test_manifest_fails_closed_when_cost_exceeds_ceiling(self):
        document = copy.deepcopy(load_manifest(MANIFEST))
        document["participants"][1]["output_usd_per_million"] = 50
        with self.assertRaisesRegex(R3ManifestError, "declared conservative cost"):
            validate_manifest(document)

    def test_offline_runner_completes_exact_solo_and_joint_call_plan(self):
        adapters = {
            "openai": _scripted_participant("openai", 2, evidence_only=True),
            "anthropic": _scripted_participant(
                "anthropic", 2, evidence_only=True
            ),
            "kimi": _scripted_participant("kimi", 4, evidence_only=True),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V7_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
            serialized = (root / "report.json").read_text(encoding="utf-8")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["experiment_id"], "r3-first-heterogeneous-v7")
        self.assertEqual(
            report["review_contract_protocol"],
            "complete_attestations_empty_events_v1",
        )
        self.assertEqual(report["actual_calls"], 8)
        self.assertEqual(report["rebase_calls"], 0)
        self.assertEqual([item["criterion_completion"] for item in report["conditions"]], [4, 4])
        self.assertTrue(
            all(item["review_protocol_compliant"] for item in report["conditions"])
        )
        self.assertEqual([item["rounds_completed"] for item in report["conditions"]], [2, 2])
        self.assertFalse(report["comparison"]["general_superiority_claimed"])
        self.assertNotIn("secret", serialized.casefold())
        for adapter in adapters.values():
            for index, prompt in enumerate(adapter.prompts):
                if index % 2 == 0:
                    self.assertIn("Mandatory round-one response bound", prompt)
                    self.assertNotIn("Mandatory review-round evidence format", prompt)
                else:
                    self.assertIn("Mandatory evidence-only review", prompt)
                    self.assertIn('"events":[]', prompt)
                    self.assertIn("Do not request tools", prompt)
                    for criterion_id in (
                        "exact_expectation",
                        "transition_automaton",
                        "first_step_equations",
                        "deterministic_verification",
                    ):
                        self.assertEqual(prompt.count(criterion_id), 2)
                    self.assertNotIn("Mandatory round-one response bound", prompt)

    def test_v4_stops_before_joint_when_solo_review_omits_attestations(self):
        adapters = {
            "openai": _scripted_participant("openai", 2),
            "anthropic": _scripted_participant("anthropic", 2),
            "kimi": PromptRecordingAdapter(
                [_response(provisional=True), _tool_only_review()],
                adapter_id="r3:kimi",
                provider="kimi",
                model="fake-kimi",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V4_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "partial_protocol_failure")
        self.assertEqual(report["actual_calls"], 2)
        self.assertEqual(len(report["conditions"]), 1)
        solo = report["conditions"][0]
        self.assertTrue(solo["provider_execution_complete"])
        self.assertFalse(solo["review_protocol_compliant"])
        self.assertFalse(solo["execution_complete"])
        self.assertEqual(
            solo["missing_review_attestations"],
            {
                "r3:kimi": [
                    "exact_expectation",
                    "transition_automaton",
                    "first_step_equations",
                    "deterministic_verification",
                ]
            },
        )
        self.assertEqual(len(adapters["openai"].prompts), 0)
        self.assertEqual(len(adapters["anthropic"].prompts), 0)

    def test_v6_stops_before_joint_when_review_adds_forbidden_events(self):
        adapters = {
            "openai": _scripted_participant("openai", 2, evidence_only=True),
            "anthropic": _scripted_participant(
                "anthropic", 2, evidence_only=True
            ),
            "kimi": PromptRecordingAdapter(
                [_response(provisional=True), _response(provisional=False)],
                adapter_id="r3:kimi",
                provider="kimi",
                model="fake-kimi",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = asyncio.run(
                run_experiment(
                    manifest=V6_MANIFEST,
                    database=root / "run.sqlite3",
                    output=root / "report.json",
                    adapters=adapters,
                )
            )
        self.assertEqual(report["status"], "partial_protocol_failure")
        self.assertEqual(report["actual_calls"], 2)
        self.assertEqual(len(report["conditions"]), 1)
        solo = report["conditions"][0]
        self.assertTrue(solo["provider_execution_complete"])
        self.assertFalse(solo["review_protocol_compliant"])
        self.assertFalse(solo["execution_complete"])
        self.assertEqual(solo["missing_review_attestations"], {})
        self.assertEqual(
            solo["unexpected_review_events"],
            {"r3:kimi": {"correction": 1, "tool_request": 1}},
        )
        self.assertEqual(len(adapters["openai"].prompts), 0)
        self.assertEqual(len(adapters["anthropic"].prompts), 0)


if __name__ == "__main__":
    unittest.main()
