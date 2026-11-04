"""v0.5.1 local structured-planning reliability tests.

Targeted coverage for work order
ZC-AMCA-V0.5.1-LOCAL-STRUCTURED-PLANNING-RELIABILITY-01:

- the existing Ollama adapter already transports native structured output
  via the documented ``ModelRequest.extra`` escape hatch (``format`` is not
  a reserved key);
- ``LocalModelPlannerRuntime`` forwards an optional ``response_format``
  into every request it builds (and rejects invalid values);
- the adaptive planner prompt carries the required response contract;
- the fail-closed parser is UNCHANGED: native format constrains the model,
  it never relaxes validation.

Real qwen3:1.7b/dolphin-llama3:8b trials are executed by the live
validation driver (see the M5.9/M5.9.1 validation evidence); these tests
keep the deterministic guarantees reproducible.
"""

import json
import unittest

from amca.intelligence import (
    ContractError,
    PLANNER_RESPONSE_FORMAT,
    ModelBackedAdaptivePlanner,
    ModelCallRequest,
    LocalModelPlannerRuntime,
)
from amca.intelligence.model_planner import _parse_model_output
from amca.intelligence.local_runtime import LocalModelPlannerRuntime as _LMPR  # noqa: F401
from amca.models.base import ModelRequest
from amca.models.ollama import OllamaAdapter


def meta_factory(record_id):
    from amca.intelligence import ContractMetadata, Sensitivity
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-v051",
        producer="v0.5.1-test",
        created_at="2026-08-28T00:00:00+00:00",
        sensitivity=Sensitivity.INTERNAL,
    )


def _planner_input():
    from amca.intelligence import PlannerInput
    return PlannerInput(
        meta=meta_factory("pin"), request_id="req-1",
        project_ref="project://demo", goal_ref="goal://demo-1",
        state_version=1, eligible_collaboration_modes=("solo",),
        budget_remaining=0.0, budget_hard_limit=True,
        feature_gates={"adaptive_planner": True, "hybrid_execution": False,
                       "nano_agents": False, "model_router": False},
    )


def model_call_request(request_id="req-1"):
    from amca.intelligence import ModelCallRequest
    return ModelCallRequest(
        meta=meta_factory("mcr"),
        request_id=request_id,
        provider_ref="local/ollama",
        system_instruction=None,
        messages=("plan",),
        temperature=0.0,
        max_output_tokens=1024,
        timeout_seconds=30.0,
        idempotency_key="idem-v051",
        privacy_mode="offline",
        approval_ref=None,
    )


class _CapturingAdapter:
    """Records every ModelRequest it is given; returns canned text."""

    model = "stub"
    local_execution = True

    def __init__(self, text='{"ok": true}'):
        self.requests = []
        self._text = text

    async def generate(self, request):
        self.requests.append(request)
        from amca.models.base import ModelResponse, TokenUsage
        return ModelResponse(
            text=self._text,
            model="stub",
            provider="stub",
            usage=TokenUsage(),
            finish_reason="stop",
        )


class NativeTransportTests(unittest.TestCase):
    def test_ollama_payload_transports_format_from_extra(self):
        adapter = OllamaAdapter(model="qwen3:1.7b")
        payload = adapter._payload(
            ModelRequest(
                messages="plan",
                model="qwen3:1.7b",
                extra={"format": PLANNER_RESPONSE_FORMAT},
            ),
            stream=False,
        )
        self.assertEqual(payload["format"], PLANNER_RESPONSE_FORMAT)

    def test_ollama_payload_reserved_keys_still_protected(self):
        adapter = OllamaAdapter(model="qwen3:1.7b")
        with self.assertRaises(ValueError):
            adapter._payload(
                ModelRequest(messages="plan", extra={"model": "evil"}),
                stream=False,
            )

    def test_runtime_forwards_response_format_into_requests(self):
        captured = _CapturingAdapter()
        runtime = LocalModelPlannerRuntime(
            adapter=captured,
            meta_factory=meta_factory,
            response_format=PLANNER_RESPONSE_FORMAT,
        )
        runtime.complete(model_call_request())
        self.assertEqual(len(captured.requests), 1)
        self.assertEqual(
            captured.requests[0].extra.get("format"),
            PLANNER_RESPONSE_FORMAT,
        )

    def test_runtime_without_response_format_sends_no_extra(self):
        captured = _CapturingAdapter()
        runtime = LocalModelPlannerRuntime(
            adapter=captured, meta_factory=meta_factory,
        )
        runtime.complete(model_call_request())
        self.assertEqual(captured.requests[0].extra, {})

    def test_invalid_response_format_rejected(self):
        with self.assertRaises(ContractError):
            LocalModelPlannerRuntime(
                adapter=_CapturingAdapter(),
                meta_factory=meta_factory,
                response_format=42,
            )


class PlannerContractTests(unittest.TestCase):
    def _planner(self, adapter):
        runtime = LocalModelPlannerRuntime(
            adapter=adapter,
            meta_factory=meta_factory,
            response_format=PLANNER_RESPONSE_FORMAT,
        )
        return ModelBackedAdaptivePlanner(
            runtime=runtime,
            meta_factory=meta_factory,
            policy_id="live-policy",
            policy_version="1",
        )

    def test_planner_prompt_carries_response_contract(self):
        from amca.intelligence import CandidateSet

        planner = self._planner(_CapturingAdapter())
        candidates = CandidateSet(
            meta=meta_factory("cands"),
            request_ref="req-1",
            candidates=(
                {"candidate_id": "cand-a", "estimated_cost": 0.0,
                 "work_unit_count": 1, "risk_class": "low",
                 "required_permissions": (), "capabilities": ("chat",)},
            ),
        )
        prompt = planner._render_prompt(None, candidates)
        payload = json.loads(prompt)
        contract = payload["response_contract"]
        self.assertEqual(contract["policy_id"], "live-policy")
        self.assertEqual(contract["policy_version"], "1")
        self.assertEqual(
            contract["response_format"],
            json.loads(json.dumps(PLANNER_RESPONSE_FORMAT)),
        )
        self.assertIn("ONLY", contract["instruction"])

    def test_structured_success_flow_with_format_constrained_model(self):
        valid = json.dumps({
            "response_version": 1,
            "policy_id": "live-policy",
            "policy_version": "1",
            "recommendations": [
                {"candidate_id": "cand-a", "model_id": "stub",
                 "confidence": 0.9, "quality_score": 0.8,
                 "reason_code": "lowest_latency"}
            ],
        })
        captured = _CapturingAdapter(text=valid)
        planner = self._planner(captured)
        from amca.intelligence import CandidateSet, PlannerResultStatus
        candidates = CandidateSet(
            meta=meta_factory("cands"), request_ref="req-1",
            candidates=({"candidate_id": "cand-a"},),
        )
        policy = type(
            "P", (), {"evaluated_envelope": {"max_total_cost": 5.0},
                      "policy_id": "live-policy", "policy_version": "1"},
        )()
        outcome = planner.propose(_planner_input(), candidates, policy)
        self.assertEqual(outcome.status, PlannerResultStatus.PROPOSED)
        # the request carried the native format constraint end-to-end
        self.assertEqual(
            captured.requests[0].extra.get("format"),
            PLANNER_RESPONSE_FORMAT,
        )

    def test_native_format_does_not_relax_parser(self):
        # schema-valid JSON but missing the required contract fields is
        # still refused: native format constrains the model, it never
        # relaxes validation
        with self.assertRaises(Exception) as ctx:
            _parse_model_output('{"ok": true}', meta_factory, "req-1")
        # fail-closed: schema-shaped-but-contract-invalid output is refused
        # (unknown response_version) exactly as prose is
        self.assertTrue(str(ctx.exception))

    def test_schema_constant_matches_parser_expectations(self):
        properties = PLANNER_RESPONSE_FORMAT["properties"]
        self.assertEqual(
            properties["response_version"], {"type": "integer", "enum": [1]}
        )
        items = properties["recommendations"]["items"]
        for field in ("candidate_id", "model_id", "confidence"):
            self.assertIn(field, items["required"])
        # the bounded score/reason fields are present as approved optional
        # properties
        for field in ("quality_score", "latency_score", "cost_score",
                      "risk_score", "reason_code"):
            self.assertIn(field, items["properties"])
        self.assertNotIn("format", PLANNER_RESPONSE_FORMAT["properties"])


if __name__ == "__main__":
    unittest.main()
