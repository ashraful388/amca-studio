"""Bounded, sanitized live-provider smoke validation.

This module supports only explicitly authorized R2 probes.
It never accepts a credential on the command line and never serializes response
text, raw SDK objects, request headers, or exception messages.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Sequence

from amca.models import (
    AnthropicAdapter,
    GeminiAdapter,
    KimiAdapter,
    ModelRequest,
    OpenAICompatibleAdapter,
)
from amca.semantic import SemanticEventExtractor


ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
INPUT_USD_PER_MILLION = 1.0
OUTPUT_USD_PER_MILLION = 5.0
MAX_OUTPUT_TOKENS = 128
MAX_API_COST_USD = 0.01
SYSTEM_PROMPT = (
    'Return only JSON with keys "summary", "events", "criterion_evidence", '
    'and "focus_resolved". Set summary exactly to "Anthropic R2 smoke passed", '
    "events and criterion_evidence to empty arrays, and focus_resolved to false."
)
USER_PROMPT = "Produce the requested AMCA R2 protocol response."
CANCELLATION_PROMPT = (
    "Write a detailed numbered explanation of how bounded cancellation keeps "
    "an asynchronous model orchestrator recoverable. Continue until stopped."
)

KIMI_MODEL = "kimi-k2.6"
KIMI_INPUT_USD_PER_MILLION = 0.16
KIMI_OUTPUT_USD_PER_MILLION = 4.0
KIMI_SYSTEM_PROMPT = (
    'Return only JSON with keys "summary", "events", "criterion_evidence", '
    'and "focus_resolved". Set summary exactly to "Kimi R2 smoke passed", '
    "events and criterion_evidence to empty arrays, and focus_resolved to false."
)
KIMI_USER_PROMPT = "Produce the requested AMCA R2 protocol response."
KIMI_CANCELLATION_PROMPT = (
    "Write a detailed numbered explanation of how bounded cancellation keeps "
    "an asynchronous multi-model orchestrator recoverable. Continue until stopped."
)

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_INPUT_USD_PER_MILLION = 0.30
GEMINI_OUTPUT_USD_PER_MILLION = 2.50
GEMINI_SYSTEM_PROMPT = (
    'Return only JSON with keys "summary", "events", "criterion_evidence", '
    'and "focus_resolved". Set summary exactly to "Gemini R2 smoke passed", '
    "events and criterion_evidence to empty arrays, and focus_resolved to false."
)
GEMINI_USER_PROMPT = "Produce the requested AMCA R2 protocol response."
GEMINI_CANCELLATION_PROMPT = (
    "Write a detailed numbered explanation of how bounded cancellation keeps "
    "an asynchronous multi-model orchestrator recoverable. Continue until stopped."
)

TABITOKEN_BASE_URL = "https://tabitoken.com/v1"
TABITOKEN_MODEL = "claude-opus-5-thinking"
TABITOKEN_MAX_OUTPUT_TOKENS = 32
TABITOKEN_PROMPT = "Say hello in one sentence."


def _preflight_cost(*, cancellation: bool = False) -> float:
    # One UTF-8 byte per input token is intentionally much more conservative
    # than the adapter's normal estimate. The output side is provider-enforced.
    prompt = CANCELLATION_PROMPT if cancellation else SYSTEM_PROMPT + USER_PROMPT
    maximum_input_tokens = len(prompt.encode("utf-8"))
    return (
        maximum_input_tokens * INPUT_USD_PER_MILLION
        + MAX_OUTPUT_TOKENS * OUTPUT_USD_PER_MILLION
    ) / 1_000_000


def _kimi_preflight_cost(*, cancellation: bool = False) -> float:
    prompt = (
        KIMI_CANCELLATION_PROMPT
        if cancellation
        else KIMI_SYSTEM_PROMPT + KIMI_USER_PROMPT
    )
    maximum_input_tokens = len(prompt.encode("utf-8"))
    return (
        maximum_input_tokens * KIMI_INPUT_USD_PER_MILLION
        + MAX_OUTPUT_TOKENS * KIMI_OUTPUT_USD_PER_MILLION
    ) / 1_000_000


def _gemini_preflight_cost(*, cancellation: bool = False) -> float:
    prompt = (
        GEMINI_CANCELLATION_PROMPT
        if cancellation
        else GEMINI_SYSTEM_PROMPT + GEMINI_USER_PROMPT
    )
    maximum_input_tokens = len(prompt.encode("utf-8"))
    return (
        maximum_input_tokens * GEMINI_INPUT_USD_PER_MILLION
        + MAX_OUTPUT_TOKENS * GEMINI_OUTPUT_USD_PER_MILLION
    ) / 1_000_000


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _safe_failure_fields(exc: BaseException) -> dict[str, Any]:
    """Expose only exception types and numeric HTTP status, never messages."""

    provider_error: BaseException = exc
    while provider_error.__cause__ is not None:
        provider_error = provider_error.__cause__
    fields: dict[str, Any] = {
        "error_class": type(exc).__name__,
        "provider_error_class": type(provider_error).__name__,
    }
    status_code = getattr(provider_error, "status_code", None)
    if status_code is None:
        status_code = getattr(provider_error, "code", None)
    if isinstance(status_code, int):
        fields["http_status"] = status_code
    return fields


async def run_anthropic_smoke(output: Path) -> dict[str, Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is unavailable in this process")
    preflight_cost = _preflight_cost()
    if preflight_cost > MAX_API_COST_USD:
        raise RuntimeError("conservative preflight exceeds the authorized cost cap")

    adapter = AnthropicAdapter(
        model=ANTHROPIC_MODEL,
        timeout=45.0,
        max_retries=0,
        default_max_output_tokens=MAX_OUTPUT_TOKENS,
        input_cost_per_million=INPUT_USD_PER_MILLION,
        output_cost_per_million=OUTPUT_USD_PER_MILLION,
    )
    request = ModelRequest.from_prompt(
        USER_PROMPT,
        system=SYSTEM_PROMPT,
        temperature=None,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        request_id="r2-anthropic-single-call",
        timeout_seconds=45.0,
    )
    started = time.monotonic()
    terminal = None
    pieces: list[str] = []
    try:
        async for chunk in adapter.stream(request):
            if chunk.delta:
                pieces.append(chunk.delta)
            if chunk.done:
                terminal = chunk
        if terminal is None or terminal.response is None:
            raise RuntimeError("stream ended without a terminal response")
        response = terminal.response
        contribution = SemanticEventExtractor().extract(
            response.text or "".join(pieces),
            source=adapter.adapter_id,
            state_version=0,
        )
        if contribution.summary != "Anthropic R2 smoke passed":
            raise RuntimeError("provider returned an unexpected protocol summary")
        if response.cost_usd > MAX_API_COST_USD:
            raise RuntimeError("provider-reported usage exceeded the authorized cap")
        report = {
            "status": "passed",
            "provider": adapter.provider,
            "model": response.model,
            "transport": "messages-stream",
            "request_count": 1,
            "automatic_retries": 0,
            "structured_response_valid": True,
            "stream_terminal_received": True,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_preflight_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
    except BaseException as exc:
        report = {
            "status": "failed",
            "provider": "anthropic",
            "model": ANTHROPIC_MODEL,
            "transport": "messages-stream",
            "request_count": 1,
            "automatic_retries": 0,
            **_safe_failure_fields(exc),
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_preflight_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
        _write_report(output, report)
        raise
    _write_report(output, report)
    return report


async def run_anthropic_cancellation(output: Path) -> dict[str, Any]:
    """Cancel one live stream after its first text delta and bound shutdown."""

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is unavailable in this process")
    preflight_cost = _preflight_cost(cancellation=True)
    if preflight_cost > MAX_API_COST_USD:
        raise RuntimeError("conservative preflight exceeds the authorized cost cap")

    adapter = AnthropicAdapter(
        model=ANTHROPIC_MODEL,
        timeout=45.0,
        max_retries=0,
        default_max_output_tokens=MAX_OUTPUT_TOKENS,
        input_cost_per_million=INPUT_USD_PER_MILLION,
        output_cost_per_million=OUTPUT_USD_PER_MILLION,
    )
    request = ModelRequest.from_prompt(
        CANCELLATION_PROMPT,
        temperature=None,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        request_id="r2-anthropic-cancellation",
        timeout_seconds=45.0,
    )
    stream = adapter.stream(request)
    first_delta = asyncio.Event()

    async def consume() -> None:
        async for chunk in stream:
            if chunk.delta:
                first_delta.set()

    started = time.monotonic()
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(first_delta.wait(), timeout=45.0)
        close_started = time.monotonic()
        cancel_returned = await adapter.cancel(request.request_id)
        try:
            await asyncio.wait_for(task, timeout=5.0)
            task_cancelled = False
        except asyncio.CancelledError:
            task_cancelled = True
        close_elapsed = time.monotonic() - close_started
        await asyncio.wait_for(stream.aclose(), timeout=2.0)
        if not cancel_returned or not task_cancelled:
            raise RuntimeError("Anthropic stream did not honor request-scoped cancellation")
        report = {
            "status": "passed",
            "provider": adapter.provider,
            "model": adapter.model,
            "transport": "messages-stream-cancellation",
            "request_count": 1,
            "automatic_retries": 0,
            "first_delta_received": True,
            "cancel_returned": cancel_returned,
            "task_cancelled": task_cancelled,
            "close_elapsed_seconds": round(close_elapsed, 6),
            "usage_terminal_received": False,
            "cost_usd": None,
            "conservative_cost_usd": preflight_cost,
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
    except BaseException as exc:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        report = {
            "status": "failed",
            "provider": "anthropic",
            "model": ANTHROPIC_MODEL,
            "transport": "messages-stream-cancellation",
            "request_count": 1,
            "automatic_retries": 0,
            **_safe_failure_fields(exc),
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
        _write_report(output, report)
        raise
    _write_report(output, report)
    return report


def _kimi_adapter() -> KimiAdapter:
    return KimiAdapter(
        model=KIMI_MODEL,
        timeout=45.0,
        input_cost_per_million=KIMI_INPUT_USD_PER_MILLION,
        output_cost_per_million=KIMI_OUTPUT_USD_PER_MILLION,
    )


async def run_kimi_smoke(output: Path) -> dict[str, Any]:
    """Validate one complete native Moonshot chat-completions stream."""

    if not os.environ.get("MOONSHOT_API_KEY"):
        raise RuntimeError("MOONSHOT_API_KEY is unavailable in this process")
    preflight_cost = _kimi_preflight_cost()
    if preflight_cost > MAX_API_COST_USD:
        raise RuntimeError("conservative preflight exceeds the authorized cost cap")
    adapter = _kimi_adapter()
    request = ModelRequest.from_prompt(
        KIMI_USER_PROMPT,
        system=KIMI_SYSTEM_PROMPT,
        temperature=None,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        request_id="r2-kimi-structured",
        timeout_seconds=45.0,
        extra={"thinking": {"type": "disabled"}},
    )
    started = time.monotonic()
    terminal = None
    pieces: list[str] = []
    try:
        async for chunk in adapter.stream(request):
            if chunk.delta:
                pieces.append(chunk.delta)
            if chunk.done:
                terminal = chunk
        if terminal is None or terminal.response is None:
            raise RuntimeError("stream ended without a terminal response")
        response = terminal.response
        contribution = SemanticEventExtractor().extract(
            response.text or "".join(pieces),
            source=adapter.adapter_id,
            state_version=0,
        )
        if contribution.summary != "Kimi R2 smoke passed":
            raise RuntimeError("provider returned an unexpected protocol summary")
        if response.cost_usd > MAX_API_COST_USD:
            raise RuntimeError("provider-reported usage exceeded the authorized cap")
        report = {
            "status": "passed",
            "provider": adapter.provider,
            "model": response.model,
            "transport": "moonshot-chat-completions-stream",
            "request_count": 1,
            "automatic_retries": 0,
            "structured_response_valid": True,
            "stream_terminal_received": True,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_preflight_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
    except BaseException as exc:
        report = {
            "status": "failed",
            "provider": "kimi",
            "model": KIMI_MODEL,
            "transport": "moonshot-chat-completions-stream",
            "request_count": 1,
            "automatic_retries": 0,
            **_safe_failure_fields(exc),
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_preflight_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
        _write_report(output, report)
        raise
    _write_report(output, report)
    return report


async def run_kimi_cancellation(output: Path) -> dict[str, Any]:
    """Cancel one Moonshot stream after its first text delta."""

    if not os.environ.get("MOONSHOT_API_KEY"):
        raise RuntimeError("MOONSHOT_API_KEY is unavailable in this process")
    preflight_cost = _kimi_preflight_cost(cancellation=True)
    if preflight_cost > MAX_API_COST_USD:
        raise RuntimeError("conservative preflight exceeds the authorized cost cap")
    adapter = _kimi_adapter()
    request = ModelRequest.from_prompt(
        KIMI_CANCELLATION_PROMPT,
        temperature=None,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        request_id="r2-kimi-cancellation",
        timeout_seconds=45.0,
        extra={"thinking": {"type": "disabled"}},
    )
    stream = adapter.stream(request)
    first_delta = asyncio.Event()

    async def consume() -> None:
        async for chunk in stream:
            if chunk.delta:
                first_delta.set()

    started = time.monotonic()
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(first_delta.wait(), timeout=45.0)
        close_started = time.monotonic()
        cancel_returned = await adapter.cancel(request.request_id)
        try:
            await asyncio.wait_for(task, timeout=5.0)
            task_cancelled = False
        except asyncio.CancelledError:
            task_cancelled = True
        close_elapsed = time.monotonic() - close_started
        await asyncio.wait_for(stream.aclose(), timeout=2.0)
        if not cancel_returned or not task_cancelled:
            raise RuntimeError("Kimi stream did not honor request-scoped cancellation")
        report = {
            "status": "passed",
            "provider": adapter.provider,
            "model": adapter.model,
            "transport": "moonshot-chat-completions-cancellation",
            "request_count": 1,
            "automatic_retries": 0,
            "first_delta_received": True,
            "cancel_returned": cancel_returned,
            "task_cancelled": task_cancelled,
            "close_elapsed_seconds": round(close_elapsed, 6),
            "usage_terminal_received": False,
            "cost_usd": None,
            "conservative_cost_usd": preflight_cost,
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
    except BaseException as exc:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        report = {
            "status": "failed",
            "provider": "kimi",
            "model": KIMI_MODEL,
            "transport": "moonshot-chat-completions-cancellation",
            "request_count": 1,
            "automatic_retries": 0,
            **_safe_failure_fields(exc),
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
        _write_report(output, report)
        raise
    _write_report(output, report)
    return report


def _gemini_adapter() -> GeminiAdapter:
    return GeminiAdapter(
        model=GEMINI_MODEL,
        timeout=45.0,
        input_cost_per_million=GEMINI_INPUT_USD_PER_MILLION,
        output_cost_per_million=GEMINI_OUTPUT_USD_PER_MILLION,
    )


async def run_gemini_smoke(output: Path) -> dict[str, Any]:
    """Validate one complete native Gemini generate-content stream."""

    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is unavailable in this process")
    preflight_cost = _gemini_preflight_cost()
    if preflight_cost > MAX_API_COST_USD:
        raise RuntimeError("conservative preflight exceeds the authorized cost cap")
    adapter = _gemini_adapter()
    request = ModelRequest.from_prompt(
        GEMINI_USER_PROMPT,
        system=GEMINI_SYSTEM_PROMPT,
        temperature=None,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        request_id="r2-gemini-structured",
        timeout_seconds=45.0,
        extra={"thinking_budget": 0},
    )
    started = time.monotonic()
    terminal = None
    pieces: list[str] = []
    try:
        async for chunk in adapter.stream(request):
            if chunk.delta:
                pieces.append(chunk.delta)
            if chunk.done:
                terminal = chunk
        if terminal is None or terminal.response is None:
            raise RuntimeError("stream ended without a terminal response")
        response = terminal.response
        contribution = SemanticEventExtractor().extract(
            response.text or "".join(pieces),
            source=adapter.adapter_id,
            state_version=0,
        )
        if contribution.summary != "Gemini R2 smoke passed":
            raise RuntimeError("provider returned an unexpected protocol summary")
        if response.cost_usd > MAX_API_COST_USD:
            raise RuntimeError("provider-reported usage exceeded the authorized cap")
        report = {
            "status": "passed",
            "provider": adapter.provider,
            "model": response.model,
            "transport": "gemini-generate-content-stream",
            "request_count": 1,
            "automatic_retries": 0,
            "structured_response_valid": True,
            "stream_terminal_received": True,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_preflight_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
    except BaseException as exc:
        report = {
            "status": "failed",
            "provider": "gemini",
            "model": GEMINI_MODEL,
            "transport": "gemini-generate-content-stream",
            "request_count": 1,
            "automatic_retries": 0,
            **_safe_failure_fields(exc),
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_preflight_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
        _write_report(output, report)
        raise
    _write_report(output, report)
    return report


async def run_gemini_cancellation(output: Path) -> dict[str, Any]:
    """Cancel one native Gemini stream after its first text delta."""

    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is unavailable in this process")
    preflight_cost = _gemini_preflight_cost(cancellation=True)
    if preflight_cost > MAX_API_COST_USD:
        raise RuntimeError("conservative preflight exceeds the authorized cost cap")
    adapter = _gemini_adapter()
    request = ModelRequest.from_prompt(
        GEMINI_CANCELLATION_PROMPT,
        temperature=None,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        request_id="r2-gemini-cancellation",
        timeout_seconds=45.0,
        extra={"thinking_budget": 0},
    )
    stream = adapter.stream(request)
    first_delta = asyncio.Event()

    async def consume() -> None:
        async for chunk in stream:
            if chunk.delta:
                first_delta.set()

    started = time.monotonic()
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(first_delta.wait(), timeout=45.0)
        close_started = time.monotonic()
        cancel_returned = await adapter.cancel(request.request_id)
        try:
            await asyncio.wait_for(task, timeout=5.0)
            task_cancelled = False
        except asyncio.CancelledError:
            task_cancelled = True
        close_elapsed = time.monotonic() - close_started
        await asyncio.wait_for(stream.aclose(), timeout=2.0)
        if not cancel_returned or not task_cancelled:
            raise RuntimeError("Gemini stream did not honor request-scoped cancellation")
        report = {
            "status": "passed",
            "provider": adapter.provider,
            "model": adapter.model,
            "transport": "gemini-generate-content-cancellation",
            "request_count": 1,
            "automatic_retries": 0,
            "first_delta_received": True,
            "cancel_returned": cancel_returned,
            "task_cancelled": task_cancelled,
            "close_elapsed_seconds": round(close_elapsed, 6),
            "usage_terminal_received": False,
            "cost_usd": None,
            "conservative_cost_usd": preflight_cost,
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
    except BaseException as exc:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        report = {
            "status": "failed",
            "provider": "gemini",
            "model": GEMINI_MODEL,
            "transport": "gemini-generate-content-cancellation",
            "request_count": 1,
            "automatic_retries": 0,
            **_safe_failure_fields(exc),
            "authorized_cost_cap_usd": MAX_API_COST_USD,
            "conservative_cost_usd": preflight_cost,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
        _write_report(output, report)
        raise
    _write_report(output, report)
    return report


async def run_tabitoken_smoke(
    output: Path, *, model: str = TABITOKEN_MODEL
) -> dict[str, Any]:
    """Run exactly one non-retrying OpenAI-compatible gateway request."""

    if not os.environ.get("TABITOKEN_API_KEY"):
        raise RuntimeError("TABITOKEN_API_KEY is unavailable in this process")
    adapter = OpenAICompatibleAdapter(
        model=model,
        base_url=TABITOKEN_BASE_URL,
        provider="tabitoken",
        api_key_env="TABITOKEN_API_KEY",
        timeout=45.0,
        allow_remote=True,
        default_headers={
            "Accept": "application/json",
            "User-Agent": "AMCA/0.1",
        },
    )
    request = ModelRequest.from_prompt(
        TABITOKEN_PROMPT,
        temperature=None,
        max_output_tokens=TABITOKEN_MAX_OUTPUT_TOKENS,
        request_id="r2-tabitoken-single-call",
        timeout_seconds=45.0,
    )
    started = time.monotonic()
    try:
        response = await adapter.generate(request)
        if not response.text.strip():
            raise RuntimeError("gateway returned an empty assistant response")
        if response.output_tokens > TABITOKEN_MAX_OUTPUT_TOKENS:
            raise RuntimeError("reported output usage exceeds the authorized limit")
        report = {
            "status": "passed",
            "provider": adapter.provider,
            "model": response.model,
            "transport": "openai-compatible-chat-completions",
            "request_count": 1,
            "automatic_retries": 0,
            "response_nonempty": True,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "authorized_max_output_tokens": TABITOKEN_MAX_OUTPUT_TOKENS,
            "pricing_verified": False,
            "cost_usd": None,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
    except BaseException as exc:
        report = {
            "status": "failed",
            "provider": "tabitoken",
            "model": model,
            "transport": "openai-compatible-chat-completions",
            "request_count": 1,
            "automatic_retries": 0,
            **_safe_failure_fields(exc),
            "authorized_max_output_tokens": TABITOKEN_MAX_OUTPUT_TOKENS,
            "pricing_verified": False,
            "cost_usd": None,
            "latency_seconds": round(time.monotonic() - started, 3),
            "credential_persisted": False,
        }
        _write_report(output, report)
        raise
    _write_report(output, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("anthropic", "gemini", "kimi", "tabitoken"),
        default="anthropic",
    )
    parser.add_argument(
        "--model",
        help="exact provider model ID; accepted only for the Tabitoken smoke",
    )
    parser.add_argument(
        "--probe",
        choices=("structured", "cancellation"),
        default="structured",
        help="Anthropic/Gemini/Kimi probe to run; ignored for Tabitoken",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".amca/r2-anthropic-smoke.json"),
    )
    args = parser.parse_args(argv)
    try:
        if args.model and args.provider != "tabitoken":
            parser.error("--model is supported only with --provider tabitoken")
        if args.provider == "tabitoken":
            report = asyncio.run(
                run_tabitoken_smoke(
                    args.output, model=args.model or TABITOKEN_MODEL
                )
            )
        elif args.provider == "gemini":
            operation = (
                run_gemini_cancellation
                if args.probe == "cancellation"
                else run_gemini_smoke
            )
            report = asyncio.run(operation(args.output))
        elif args.provider == "kimi":
            operation = (
                run_kimi_cancellation
                if args.probe == "cancellation"
                else run_kimi_smoke
            )
            report = asyncio.run(operation(args.output))
        else:
            operation = (
                run_anthropic_cancellation
                if args.probe == "cancellation"
                else run_anthropic_smoke
            )
            report = asyncio.run(operation(args.output))
    except BaseException as exc:
        print(f"{args.provider} R2 smoke failed safely: {type(exc).__name__}")
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
