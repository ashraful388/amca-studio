from __future__ import annotations

import json
import unittest

from amca.domain import EventType
from amca.semantic import (
    SemanticEventExtractor,
    SemanticExtractionError,
    is_model_authorable_event_type,
)


class SemanticExtractorTests(unittest.TestCase):
    def test_model_authorable_types_exclude_system_errors(self) -> None:
        self.assertTrue(is_model_authorable_event_type(EventType.CORRECTION))
        self.assertTrue(is_model_authorable_event_type(EventType.OBJECTION))
        self.assertFalse(is_model_authorable_event_type(EventType.MODEL_ERROR))

    def test_extracts_structured_events(self) -> None:
        text = json.dumps(
            {
                "summary": "Boundary condition needs checking.",
                "events": [
                    {
                        "type": "objection",
                        "content": "The zero case is not covered.",
                        "severity": "high",
                        "confidence": 0.9,
                        "evidence_refs": [],
                        "proposed_check": "Evaluate n=0.",
                    }
                ],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )
        result = SemanticEventExtractor().extract(text, source="model-a", state_version=3)
        self.assertEqual(result.events[0].event_type, EventType.OBJECTION)
        self.assertEqual(result.events[0].state_version, 3)
        self.assertFalse(result.focus_resolved)

    def test_invalid_text_is_not_guessed_into_a_claim(self) -> None:
        with self.assertRaises(SemanticExtractionError):
            SemanticEventExtractor().extract("I think it works", source="m", state_version=0)

    def test_unknown_event_type_is_dropped_and_counted_not_fatal(self) -> None:
        text = json.dumps(
            {
                "summary": "The capital of France is Paris.",
                "events": [
                    {"type": "grand_insight", "content": "Paris matters."},
                    {"type": "hypothesis", "content": "Paris is the capital."},
                ],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )
        result = SemanticEventExtractor().extract(text, source="model-a", state_version=1)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].event_type, EventType.HYPOTHESIS)
        self.assertEqual(result.dropped_event_count, 1)

    def test_non_authorable_type_is_dropped_and_counted(self) -> None:
        text = json.dumps(
            {
                "summary": "Status update.",
                "events": [
                    {"type": "equation_update", "content": "x = 2"},
                    {"type": "model_error", "content": "fake system event"},
                ],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )
        result = SemanticEventExtractor().extract(text, source="m", state_version=0)
        self.assertEqual(result.events, ())
        self.assertEqual(result.dropped_event_count, 2)

    def test_malformed_known_typed_events_still_fail_closed(self) -> None:
        base = {"summary": "s", "events": [], "criterion_evidence": [], "focus_resolved": False}
        # CLOSURE-05B Part C: a well-typed event with EMPTY content carries
        # no claim to lose — it is dropped and counted (like unknown type
        # names), never destroying an otherwise-valid contribution.
        missing_content = dict(base, events=[{"type": "hypothesis"}])
        extracted = SemanticEventExtractor().extract(
            json.dumps(missing_content), source="m", state_version=0
        )
        self.assertEqual(extracted.summary, "s")
        self.assertEqual(extracted.events, ())
        self.assertEqual(extracted.dropped_event_count, 1)
        bad_confidence = dict(
            base,
            events=[{"type": "answer", "content": "x", "confidence": 5}],
        )
        with self.assertRaises(SemanticExtractionError):
            SemanticEventExtractor().extract(json.dumps(bad_confidence), source="m", state_version=0)

    def test_tool_request_preserves_goal_criterion_link(self) -> None:
        text = json.dumps(
            {
                "summary": "Request deterministic evidence.",
                "events": [
                    {
                        "type": "tool_request",
                        "content": "Evaluate 9 + 8.",
                        "severity": "info",
                        "confidence": 1,
                        "evidence_refs": [],
                        "tool": "math",
                        "operation": "evaluate",
                        "expression": "9 + 8",
                        "criterion_id": "numeric-result",
                    }
                ],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )

        result = SemanticEventExtractor().extract(text, source="model", state_version=4)

        self.assertEqual(result.events[0].payload["criterion_id"], "numeric-result")


if __name__ == "__main__":
    unittest.main()
