"""M0 Contract Foundation tests.

Focused coverage required by the M0 work order: contract construction,
canonical serialization, digest consistency, round-trip serialization and
recovery, unknown-version rejection, default feature-gate states, and
deterministic compatibility of v0.4.0 decision projections.
"""

import unittest

from amca.intelligence import (
    AGENT_LIFECYCLE_TRANSITIONS,
    DEFAULT_GATES,
    LEAST_AUTHORITY_DEFAULTS,
    AgentAssignment,
    AgentLifecycleState,
    AgentOrigin,
    CapabilitySource,
    ConfidenceAssessment,
    ConfidenceBand,
    ContractError,
    ContractErrorCode,
    CostMetadata,
    CriterionEvaluation,
    DeterministicDecisionProjection,
    EvidenceKind,
    ExecutionEvidence,
    ExecutionStrategy,
    FeatureGateRegistry,
    ModelCallRequest,
    ModelCallResponse,
    ModelCapabilityMetadata,
    ModelFailureCategory,
    OverallDisposition,
    PlannerFailure,
    PlannerFailureCategory,
    PlannerInput,
    PlannerResult,
    PlannerResultStatus,
    ProviderDescriptor,
    ProvenanceRecord,
    Sensitivity,
    TaskRequest,
    ValidationStatus,
    WorkUnit,
    canonical_json,
    content_digest,
    gates_from_dict,
    least_authority_defaults,
    project_v04_decision,
    validate_agent_transition,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta(record_id="rec-1", correlation="corr-1", producer="m0-test",
         sensitivity=Sensitivity.INTERNAL, created=CREATED):
    from amca.intelligence import ContractMetadata
    return ContractMetadata(
        record_id=record_id,
        correlation_id=correlation,
        producer=producer,
        created_at=created,
        sensitivity=sensitivity,
    )


def work_unit(unit_id="unit-1", depends_on=()):
    return WorkUnit(
        meta=meta("wu-" + unit_id),
        unit_id=unit_id,
        depends_on=tuple(depends_on),
        focus="summarize sources",
        assigned_agent_ref="agent://researcher",
        verifier_ref="verifier://citation-check",
        max_retries=1,
    )


def strategy():
    return ExecutionStrategy(
        meta=meta("strat-1"),
        planner_kind="deterministic",
        planner_version="v0.4.0",
        collaboration_mode="solo",
        synchronization_mode="sequential",
        work_units=(work_unit("unit-1"), work_unit("unit-2", ("unit-1",))),
        max_rounds=2,
        max_total_cost=0.0,
        stop_condition="all units verified",
        fallback_strategy_ref=None,
    )


def task_request(**overrides):
    fields = dict(
        meta=meta("req-1"),
        request_id="req-1",
        objective="Summarize the provided sources offline.",
        requested_outputs=("summary.md",),
        constraints={"privacy_mode": "offline"},
        actor_ref="user://local",
        goal_contract_ref="goal://demo-1",
    )
    fields.update(overrides)
    return TaskRequest(**fields)


def planner_result(status=PlannerResultStatus.PROPOSED, with_failure=False):
    failure = None
    if with_failure:
        failure = PlannerFailure(
            meta=meta("fail-1"),
            category=PlannerFailureCategory.NO_FEASIBLE_STRATEGY,
            message="no deterministic strategy satisfies the constraints",
            retryable=False,
            deterministic_fallback=True,
        )
    return PlannerResult(
        meta=meta("result-1"),
        request_ref="req-1",
        status=status,
        strategy=strategy() if status is PlannerResultStatus.PROPOSED else None,
        alternatives=(),
        failure=failure,
    )


class CanonicalSerializationTests(unittest.TestCase):
    def test_canonical_json_is_key_order_independent(self):
        first = canonical_json({"b": 1, "a": {"d": 2, "c": 3}})
        second = canonical_json({"a": {"c": 3, "d": 2}, "b": 1})
        self.assertEqual(first, second)
        self.assertEqual(first, '{"a":{"c":3,"d":2},"b":1}')

    def test_non_finite_numbers_are_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            canonical_json({"cost": float("inf")})
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)

    def test_digest_is_consistent_and_sensitive(self):
        left = content_digest({"mode": "solo", "models": ["local-a"]})
        right = content_digest({"models": ["local-a"], "mode": "solo"})
        self.assertEqual(left, right)
        self.assertEqual(len(left), 64)
        self.assertNotEqual(left, content_digest({"mode": "joint",
                                                  "models": ["local-a"]}))


class ContractConstructionTests(unittest.TestCase):
    def test_planner_boundary_constructs(self):
        request = task_request()
        self.assertEqual(request.CONTRACT_NAME, "amca.intelligence.task_request")
        self.assertEqual(request.meta.sensitivity, Sensitivity.INTERNAL)
        result = planner_result()
        self.assertEqual(result.status, PlannerResultStatus.PROPOSED)
        self.assertEqual(result.strategy.max_rounds, 2)

    def test_agent_boundary_constructs(self):
        assignment = AgentAssignment(
            meta=meta("asg-1"),
            assignment_id="asg-1",
            agent_ref="agent://researcher",
            work_unit_ref="unit-1",
            focus="collect sources",
            acceptance_criteria=("every claim carries evidence",),
            input_refs=("input://objective",),
            evidence_refs=(),
            dependency_unit_ids=(),
            allowed_tools=("local_search",),
            max_retries=0,
            deadline=CREATED,
            policy_version="v0.4.0",
            feature_gate_version=1,
        )
        self.assertEqual(
            assignment.CONTRACT_NAME, "amca.intelligence.agent_assignment"
        )
        self.assertEqual(assignment.meta.sensitivity, Sensitivity.INTERNAL)

    def test_model_boundary_constructs(self):
        descriptor = ProviderDescriptor(
            meta=meta("prov-1"),
            provider_id="ollama/local",
            adapter_name="ollama",
            model_id="llama3:8b",
            endpoint_class="local_http",
            local_execution=True,
            configuration_provenance="declared by user",
            healthy=True,
        )
        capabilities = ModelCapabilityMetadata(
            meta=meta("cap-1"),
            provider_ref="ollama/local",
            streaming=True,
            tool_calling=False,
            vision=False,
            json_mode=True,
            system_messages=True,
            local_execution=True,
            context_window=8192,
            max_output_tokens=4096,
            capability_source=CapabilitySource.DECLARED,
            observed_at=CREATED,
        )
        request = ModelCallRequest(
            meta=meta("mreq-1"),
            request_id="mreq-1",
            provider_ref="ollama/local",
            messages=("Summarize the sources.",),
            system_instruction=None,
            temperature=0.2,
            max_output_tokens=512,
            timeout_seconds=30.0,
            idempotency_key="idem-1",
            privacy_mode="offline",
            approval_ref=None,
        )
        response = ModelCallResponse(
            meta=meta("mres-1"),
            request_ref="mreq-1",
            provider_ref="ollama/local",
            resolved_model_id="llama3:8b",
            text_output="Summary text.",
            finish_reason="stop",
            input_tokens=120,
            output_tokens=64,
            latency_ms=410.5,
            cost=CostMetadata(
                meta=meta("cost-1"),
                currency="USD",
                preflight_upper_bound=0.0,
                estimated_cost=0.0,
                measured_cost=0.0,
                pricing_source="local",
                pricing_known=True,
                settled=True,
            ),
            local_execution=True,
            failure_category=None,
        )
        self.assertEqual(descriptor.model_id, "llama3:8b")
        self.assertEqual(capabilities.context_window, 8192)
        self.assertEqual(request.privacy_mode, "offline")
        self.assertEqual(response.cost.settled, True)

    def test_evidence_boundary_constructs(self):
        evidence = ExecutionEvidence(
            meta=meta("ev-1"),
            evidence_id="ev-1",
            kind=EvidenceKind.SOURCE,
            summary="Source excerpt retrieved offline",
            producer="tool://local_search",
            project_ref="project://demo",
            task_ref="unit-1",
            method="local_search excerpt",
            content_digest_value="a" * 64,
            provenance_ref=None,
            validation=ValidationStatus(
                meta=meta("val-1"),
                criterion_evaluation=CriterionEvaluation.PASS,
                overall=OverallDisposition.VERIFIED,
                verifier_supported=True,
                waiver_ref=None,
            ),
            confidence=None,
        )
        self.assertEqual(evidence.kind, EvidenceKind.SOURCE)
        self.assertEqual(
            evidence.validation.overall, OverallDisposition.VERIFIED
        )


class ContractValidationTests(unittest.TestCase):
    def test_empty_required_strings_fail_closed(self):
        with self.assertRaises(ContractError) as ctx:
            task_request(request_id="")
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)

    def test_bad_timestamp_fails_closed(self):
        with self.assertRaises(ContractError):
            task_request(meta=meta(created="not-a-timestamp"))
        with self.assertRaises(ContractError):
            task_request(meta=meta(created="2026-08-27T00:00:00"))

    def test_unknown_enum_value_rejected_on_construction(self):
        with self.assertRaises(ContractError) as ctx:
            planner_result(status="detonated")
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)

    def test_proposed_result_requires_strategy(self):
        with self.assertRaises(ContractError) as ctx:
            PlannerResult(
                meta=meta("result-2"),
                request_ref="req-1",
                status=PlannerResultStatus.PROPOSED,
                strategy=None,
                alternatives=(),
                failure=None,
            )
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)

    def test_blocked_result_requires_failure_record(self):
        with self.assertRaises(ContractError):
            planner_result(status=PlannerResultStatus.BLOCKED)

    def test_unknown_dependency_fails_closed(self):
        with self.assertRaises(ContractError) as ctx:
            ExecutionStrategy(
                meta=meta("strat-2"),
                planner_kind="deterministic",
                planner_version="v0.4.0",
                collaboration_mode="parallel",
                synchronization_mode="sequential",
                work_units=(work_unit("unit-1", ("ghost",)),),
                max_rounds=1,
                max_total_cost=0.0,
                stop_condition="done",
                fallback_strategy_ref=None,
            )
        self.assertIn("unknown units", ctx.exception.message)

    def test_credential_material_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            task_request(constraints={"api_key": "sk-super-secret"})
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)

    def test_token_count_fields_are_not_false_positives(self):
        request = ModelCallRequest(
            meta=meta("mreq-2"),
            request_id="mreq-2",
            provider_ref="ollama/local",
            messages=("hi",),
            system_instruction=None,
            temperature=0.0,
            max_output_tokens=128,
            timeout_seconds=5.0,
            idempotency_key="idem-2",
            privacy_mode="offline",
            approval_ref=None,
        )
        self.assertEqual(request.max_output_tokens, 128)

    def test_unknown_pricing_requires_conservative_bound(self):
        with self.assertRaises(ContractError):
            CostMetadata(
                meta=meta("cost-2"),
                currency="USD",
                preflight_upper_bound=None,
                estimated_cost=None,
                measured_cost=None,
                pricing_source=None,
                pricing_known=False,
                settled=False,
            )

    def test_validation_status_invariants(self):
        with self.assertRaises(ContractError):
            ValidationStatus(
                meta=meta("val-2"),
                criterion_evaluation=CriterionEvaluation.FAIL,
                overall=OverallDisposition.VERIFIED,
                verifier_supported=False,
                waiver_ref=None,
            )


class RoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_planner_result(self):
        original = planner_result()
        restored = PlannerResult.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(
            restored.content_digest(), original.content_digest()
        )

    def test_round_trip_preserves_nested_strategy_and_work_units(self):
        original = strategy()
        restored = ExecutionStrategy.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.work_units[1].depends_on, ("unit-1",))

    def test_round_trip_preserves_evidence_chain(self):
        original = ExecutionEvidence(
            meta=meta("ev-2"),
            evidence_id="ev-2",
            kind=EvidenceKind.OBSERVATION,
            summary="deterministic routing observed",
            producer="planner://deterministic",
            project_ref="project://demo",
            task_ref=None,
            method="runtime observation",
            content_digest_value="b" * 64,
            provenance_ref=None,
            validation=None,
            confidence=None,
        )
        restored = ExecutionEvidence.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_unknown_contract_version_rejected(self):
        data = planner_result().to_dict()
        data["contract_version"] = 2
        with self.assertRaises(ContractError) as ctx:
            PlannerResult.from_dict(data)
        self.assertIn("unsupported contract_version", ctx.exception.message)

    def test_unknown_contract_name_rejected(self):
        data = task_request().to_dict()
        data["contract_name"] = "amca.intelligence.something_else"
        with self.assertRaises(ContractError) as ctx:
            TaskRequest.from_dict(data)
        self.assertIn("contract_name mismatch", ctx.exception.message)

    def test_unknown_enum_value_rejected_on_read(self):
        data = planner_result().to_dict()
        data["status"] = "teleported"
        with self.assertRaises(ContractError):
            PlannerResult.from_dict(data)


class FeatureGateTests(unittest.TestCase):
    def test_all_gates_default_off(self):
        registry = DEFAULT_GATES
        self.assertEqual(
            registry.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )

    def test_registry_is_passive_and_immutable(self):
        with self.assertRaises(AttributeError):
            DEFAULT_GATES.adaptive_planner = True
        enabled = DEFAULT_GATES.with_overrides(adaptive_planner=True)
        self.assertFalse(DEFAULT_GATES.adaptive_planner)
        self.assertTrue(enabled.adaptive_planner)
        self.assertFalse(enabled.hybrid_execution)

    def test_unknown_gate_name_fails_closed(self):
        with self.assertRaises(ContractError):
            DEFAULT_GATES.with_overrides(nano_router=True)
        with self.assertRaises(ContractError):
            DEFAULT_GATES.is_enabled("not_a_gate")

    def test_serialization_round_trip_and_strict_reads(self):
        restored = gates_from_dict(DEFAULT_GATES.to_dict())
        self.assertEqual(restored, DEFAULT_GATES)
        with self.assertRaises(ContractError):
            gates_from_dict({"adaptive_planner": False, "mystery_gate": True})
        with self.assertRaises(ContractError):
            gates_from_dict({"adaptive_planner": "yes"})
        self.assertEqual(
            gates_from_dict({"adaptive_planner": True}).adaptive_planner, True
        )


class DeterministicCompatibilityTests(unittest.TestCase):
    def test_v04_decision_projects_without_meaning_change(self):
        decision = {
            "kind": "collaboration",
            "mode": "parallel_decomposition",
            "models": ("ollama/llama3:8b", "ollama/qwen2.5:14b"),
            "reasons": ("independent subtasks detected",),
            "state_version": 12,
        }
        projection = project_v04_decision(
            decision, meta=meta("proj-1"), source_record_kind="collaboration_decision"
        )
        # Decisions are preserved verbatim up to JSON normalization (tuples
        # round-trip as lists), so equivalence is asserted canonically.
        self.assertEqual(
            canonical_json(projection.decision_fields()), canonical_json(decision)
        )
        self.assertEqual(
            projection.decision_digest, content_digest(decision)
        )
        self.assertEqual(
            projection.defaults_applied, least_authority_defaults()
        )
        restored = DeterministicDecisionProjection.from_dict(
            projection.to_dict()
        )
        self.assertEqual(restored, projection)
        self.assertEqual(restored.decision_fields()["mode"], "parallel_decomposition")

    def test_absent_v05_fields_get_least_authority_defaults(self):
        defaults = least_authority_defaults()
        self.assertEqual(defaults, LEAST_AUTHORITY_DEFAULTS)
        self.assertFalse(defaults["adaptive_enabled"])
        self.assertFalse(defaults["hybrid_enabled"])
        self.assertFalse(defaults["nano_agents_enabled"])
        self.assertFalse(defaults["model_routing_evidence"])
        self.assertEqual(defaults["cloud_calls"], 0)
        self.assertEqual(defaults["frontier_calls"], 0)
        self.assertEqual(defaults["child_agents"], 0)
        self.assertEqual(defaults["max_retries"], 0)
        self.assertEqual(defaults["max_rounds"], 1)

    def test_projection_rejects_tampered_digest(self):
        projection = project_v04_decision(
            {"kind": "routing", "mode": "solo"},
            meta=meta("proj-2"),
            source_record_kind="routing_decision",
        )
        data = projection.to_dict()
        data["decision_digest"] = "0" * 64
        with self.assertRaises(ContractError) as ctx:
            DeterministicDecisionProjection.from_dict(data)
        self.assertEqual(
            ctx.exception.code, ContractErrorCode.INTEGRITY_VIOLATION
        )

    def test_projection_rejects_widened_defaults(self):
        projection = project_v04_decision(
            {"kind": "routing", "mode": "solo"},
            meta=meta("proj-3"),
            source_record_kind="routing_decision",
        )
        data = projection.to_dict()
        data["defaults_applied"] = dict(LEAST_AUTHORITY_DEFAULTS, cloud_calls=99)
        with self.assertRaises(ContractError):
            DeterministicDecisionProjection.from_dict(data)

    def test_old_records_replay_without_v05_metadata(self):
        # A v0.4.0-era decision carries no v0.5.0 fields at all; projection
        # must still succeed with defaults and preserve the record verbatim.
        bare = {"mode": "solo", "models": ["local-a"]}
        projection = project_v04_decision(
            bare, meta=meta("proj-4"), source_record_kind="model_set_decision"
        )
        self.assertEqual(projection.decision_fields(), bare)


class AgentLifecycleTests(unittest.TestCase):
    def test_happy_path_transitions_are_allowed(self):
        path = [
            AgentLifecycleState.PROPOSED,
            AgentLifecycleState.AUTHORIZED,
            AgentLifecycleState.QUEUED,
            AgentLifecycleState.STARTING,
            AgentLifecycleState.RUNNING,
            AgentLifecycleState.VERIFYING,
            AgentLifecycleState.COMPLETED,
            AgentLifecycleState.MERGED,
            AgentLifecycleState.RETIRED,
        ]
        for previous, next_state in zip(path, path[1:]):
            validate_agent_transition(previous, next_state)

    def test_invalid_transitions_fail_closed(self):
        invalid = [
            (AgentLifecycleState.COMPLETED, AgentLifecycleState.RUNNING),
            (AgentLifecycleState.PROPOSED, AgentLifecycleState.RUNNING),
            (AgentLifecycleState.MERGED, AgentLifecycleState.QUEUED),
            (AgentLifecycleState.CANCELLED, AgentLifecycleState.RUNNING),
            (AgentLifecycleState.RETIRED, AgentLifecycleState.QUEUED),
        ]
        for previous, next_state in invalid:
            with self.assertRaises(ContractError):
                validate_agent_transition(previous, next_state)

    def test_transition_map_is_fail_closed_by_default(self):
        for state, allowed in AGENT_LIFECYCLE_TRANSITIONS.items():
            for other in AgentLifecycleState:
                if other not in allowed:
                    with self.assertRaises(ContractError):
                        validate_agent_transition(state, other)

    def test_failed_retry_is_bounded_to_queued(self):
        self.assertEqual(
            AGENT_LIFECYCLE_TRANSITIONS[AgentLifecycleState.FAILED],
            frozenset({AgentLifecycleState.QUEUED}),
        )
        with self.assertRaises(ContractError):
            validate_agent_transition(
                AgentLifecycleState.FAILED, AgentLifecycleState.RUNNING
            )


if __name__ == "__main__":
    unittest.main()
