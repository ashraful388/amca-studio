"""Offline contract tests for the one-request R2 smoke harness."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amca.models import ModelResponse, StreamChunk, TokenUsage
from amca.provider_smoke import (
    MAX_API_COST_USD,
    _gemini_preflight_cost,
    _preflight_cost,
    _kimi_preflight_cost,
    run_anthropic_cancellation,
    run_anthropic_smoke,
    run_gemini_cancellation,
    run_gemini_smoke,
    run_kimi_cancellation,
    run_kimi_smoke,
    run_tabitoken_smoke,
)


class FakeAnthropicAdapter:
    provider = "anthropic"
    adapter_id = "anthropic:fake"

    def __init__(self, **kwargs):
        self.model = kwargs["model"]

    async def stream(self, request):
        yield StreamChunk(delta='{"summary":"Anthropic R2 smoke passed",')
        yield StreamChunk(
            done=True,
            response=ModelResponse(
                text=(
                    '{"summary":"Anthropic R2 smoke passed","events":[],'
                    '"criterion_evidence":[],"focus_resolved":false}'
                ),
                model=self.model,
                provider=self.provider,
                usage=TokenUsage(input_tokens=20, output_tokens=20, cost_usd=0.0006),
            ),
        )


class FakeBlockingAnthropicAdapter:
    provider = "anthropic"
    adapter_id = "anthropic:fake-blocking"

    def __init__(self, **kwargs):
        self.model = kwargs["model"]
        self.task = None

    async def stream(self, request):
        self.task = asyncio.current_task()
        yield StreamChunk(delta="first")
        await asyncio.Event().wait()

    async def cancel(self, request_id=None):
        if self.task is None:
            return False
        self.task.cancel()
        return True


class FakeKimiAdapter(FakeAnthropicAdapter):
    provider = "kimi"
    adapter_id = "kimi:fake"

    async def stream(self, request):
        yield StreamChunk(delta='{"summary":"Kimi R2 smoke passed",')
        yield StreamChunk(
            done=True,
            response=ModelResponse(
                text=(
                    '{"summary":"Kimi R2 smoke passed","events":[],'
                    '"criterion_evidence":[],"focus_resolved":false}'
                ),
                model=self.model,
                provider=self.provider,
                usage=TokenUsage(
                    input_tokens=20,
                    output_tokens=20,
                    cost_usd=0.0001,
                ),
            ),
        )


class FakeBlockingKimiAdapter(FakeBlockingAnthropicAdapter):
    provider = "kimi"
    adapter_id = "kimi:fake-blocking"


class FakeGeminiAdapter(FakeAnthropicAdapter):
    provider = "gemini"
    adapter_id = "gemini:fake"

    async def stream(self, request):
        yield StreamChunk(delta='{"summary":"Gemini R2 smoke passed",')
        yield StreamChunk(
            done=True,
            response=ModelResponse(
                text=(
                    '{"summary":"Gemini R2 smoke passed","events":[],'
                    '"criterion_evidence":[],"focus_resolved":false}'
                ),
                model=self.model,
                provider=self.provider,
                usage=TokenUsage(
                    input_tokens=20,
                    output_tokens=20,
                    cost_usd=0.0001,
                ),
            ),
        )


class FakeBlockingGeminiAdapter(FakeBlockingAnthropicAdapter):
    provider = "gemini"
    adapter_id = "gemini:fake-blocking"


class FakeTabitokenAdapter:
    provider = "tabitoken"
    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.model = kwargs["model"]

    async def generate(self, request):
        return ModelResponse(
            text="Hello from the gateway.",
            model=self.model,
            provider=self.provider,
            usage=TokenUsage(input_tokens=8, output_tokens=5),
        )


class ProviderSmokeTests(unittest.TestCase):
    def test_anthropic_preflight_is_below_authorized_cap(self):
        self.assertGreater(_preflight_cost(), 0)
        self.assertLess(_preflight_cost(), MAX_API_COST_USD)
        self.assertLess(
            _preflight_cost() + _preflight_cost(cancellation=True),
            MAX_API_COST_USD,
        )

    def test_kimi_preflight_is_below_aggregate_authorized_cap(self):
        self.assertGreater(_kimi_preflight_cost(), 0)
        self.assertLess(
            _kimi_preflight_cost() + _kimi_preflight_cost(cancellation=True),
            MAX_API_COST_USD,
        )

    def test_gemini_preflight_is_below_aggregate_authorized_cap(self):
        self.assertGreater(_gemini_preflight_cost(), 0)
        self.assertLess(
            _gemini_preflight_cost() + _gemini_preflight_cost(cancellation=True),
            MAX_API_COST_USD,
        )

    def test_success_report_is_structured_and_contains_no_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with (
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "canary-secret"}),
                patch("amca.provider_smoke.AnthropicAdapter", FakeAnthropicAdapter),
            ):
                import asyncio

                report = asyncio.run(run_anthropic_smoke(output))
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual(report["request_count"], 1)
            self.assertEqual(json.loads(serialized)["status"], "passed")
            self.assertNotIn("canary-secret", serialized)

    def test_anthropic_cancellation_report_is_bounded_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cancel.json"
            with (
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "canary-secret"}),
                patch(
                    "amca.provider_smoke.AnthropicAdapter",
                    FakeBlockingAnthropicAdapter,
                ),
            ):
                report = asyncio.run(run_anthropic_cancellation(output))
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["task_cancelled"])
            self.assertNotIn("canary-secret", serialized)

    def test_kimi_reports_are_bounded_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.dict(os.environ, {"MOONSHOT_API_KEY": "canary-secret"}),
                patch("amca.provider_smoke.KimiAdapter", FakeKimiAdapter),
            ):
                structured = asyncio.run(run_kimi_smoke(root / "structured.json"))
            with (
                patch.dict(os.environ, {"MOONSHOT_API_KEY": "canary-secret"}),
                patch(
                    "amca.provider_smoke.KimiAdapter",
                    FakeBlockingKimiAdapter,
                ),
            ):
                cancelled = asyncio.run(run_kimi_cancellation(root / "cancel.json"))
            serialized = (root / "structured.json").read_text(encoding="utf-8")
            serialized += (root / "cancel.json").read_text(encoding="utf-8")
            self.assertEqual(structured["status"], "passed")
            self.assertTrue(cancelled["task_cancelled"])
            self.assertNotIn("canary-secret", serialized)

    def test_gemini_reports_are_bounded_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "canary-secret"}),
                patch("amca.provider_smoke.GeminiAdapter", FakeGeminiAdapter),
            ):
                structured = asyncio.run(run_gemini_smoke(root / "structured.json"))
            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "canary-secret"}),
                patch(
                    "amca.provider_smoke.GeminiAdapter",
                    FakeBlockingGeminiAdapter,
                ),
            ):
                cancelled = asyncio.run(run_gemini_cancellation(root / "cancel.json"))
            serialized = (root / "structured.json").read_text(encoding="utf-8")
            serialized += (root / "cancel.json").read_text(encoding="utf-8")
            self.assertEqual(structured["status"], "passed")
            self.assertTrue(cancelled["task_cancelled"])
            self.assertNotIn("canary-secret", serialized)

    def test_tabitoken_report_enforces_one_call_and_32_output_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with (
                patch.dict(os.environ, {"TABITOKEN_API_KEY": "canary-secret"}),
                patch(
                    "amca.provider_smoke.OpenAICompatibleAdapter",
                    FakeTabitokenAdapter,
                ),
            ):
                import asyncio

                report = asyncio.run(
                    run_tabitoken_smoke(output, model="claude-opus-4.8")
                )
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual(report["request_count"], 1)
            self.assertEqual(report["authorized_max_output_tokens"], 32)
            self.assertIsNone(report["cost_usd"])
            self.assertEqual(report["model"], "claude-opus-4.8")
            self.assertNotIn("canary-secret", serialized)
            self.assertEqual(
                FakeTabitokenAdapter.last_kwargs["default_headers"],
                {"Accept": "application/json", "User-Agent": "AMCA/0.1"},
            )


if __name__ == "__main__":
    unittest.main()
