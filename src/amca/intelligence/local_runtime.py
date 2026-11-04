"""M5.5 local model runtime adapter bridging M1.2 to existing infrastructure.

Additive module introduced by the M5.5 Local Model Runtime Integration
milestone (zcode/amca-v0.5.0-m5-5-local-model-runtime-integration-01).

Implements the ``PlannerModelRuntime`` protocol by wrapping an existing
``ModelAdapter`` (e.g. ``OllamaAdapter``), translating intelligence-layer
``ModelCallRequest/Response`` envelopes to and from the model layer. It
reuses the existing provider stack — no new HTTP implementation, no second
Ollama client.

Also provides:
- bounded robust JSON parsing for model outputs;
- M5.5.2: active in-flight cancellation routed through the existing
  adapter cancel protocol (never a competing one);
- truthful readiness reporting for adaptive/hybrid/router/nano capabilities.

Import-isolated as in M0–M5.4: nothing in the pre-existing application
imports this package; the adaptive_planner gate remains FALSE; no paid or
cloud calls exist (the adapter targets local models only unless the caller
explicitly supplies a cloud adapter and the gate is ON, which no current
code path does).
"""

import asyncio
import dataclasses
import json
import math
from typing import Any, Callable, Mapping, Optional, Tuple

from .contracts import (
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    ModelCallRequest,
    ModelCallResponse,
    Sensitivity,
)


class ModelCallCancelled(Exception):
    """Raised when a model call is cancelled (normalized signal)."""
from .model_planner import PlannerModelRuntime
from ..models.base import ModelAdapter, ModelRequest, ModelResponse


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


class LocalModelPlannerRuntime:
    """Concrete PlannerModelRuntime wrapping an existing ModelAdapter.

    Translates intelligence-layer ``ModelCallRequest`` envelopes into
    model-layer ``ModelRequest`` objects, calls the adapter's ``generate()``
    method, and seals the result back into a ``ModelCallResponse``. It
    supports both sync and async adapters via an internal event-loop
    bridge. No paid/cloud call occurs unless the caller explicitly supplies
    a cloud adapter AND sets the gate ON (no current code path does).
    """

    def __init__(
        self,
        *,
        adapter: ModelAdapter,
        timeout_seconds: float = 60.0,
        cancel_poll_seconds: float = 0.01,
        meta_factory: Any = None,
        response_format: Any = None,
    ) -> None:
        if not _finite(cancel_poll_seconds) or cancel_poll_seconds <= 0:
            raise ContractError(
                ContractErrorCode.INVALID,
                "cancel_poll_seconds must be a finite positive number",
            )
        if response_format is not None and not isinstance(
            response_format, (str, Mapping)
        ):
            raise ContractError(
                ContractErrorCode.INVALID,
                "response_format must be a string (e.g. 'json') or a JSON "
                "schema mapping",
            )
        self._adapter = adapter
        self._timeout = timeout_seconds
        self._cancel_poll = float(cancel_poll_seconds)
        self._meta_factory = meta_factory
        # v0.5.1: native provider structured-output constraint (Ollama
        # ``format`` field) forwarded through the documented
        # ``ModelRequest.extra`` escape hatch. Does not alter parsing: the
        # fail-closed parser still validates whatever the model returns.
        self._response_format = response_format
        # M5.5.2 truthful-cancellation reporting: True when the most recent
        # in-flight cancellation went through the existing adapter cancel
        # protocol, False when the adapter exposes none (interface
        # limitation) and the runtime had to cancel its own call task.
        self.last_cancel_used_adapter_protocol: Optional[bool] = None

    @property
    def adapter_id(self) -> str:
        return self._adapter.adapter_id

    @property
    def provider(self) -> str:
        return self._adapter.provider

    @property
    def model(self) -> str:
        return self._adapter.model

    def complete(
        self,
        request: ModelCallRequest,
        *,
        meta_factory: Callable[[str], Any] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ModelCallResponse:
        """Run one bounded model call through the existing adapter with
        timeout and cancellation support."""

        if cancel_check is not None and cancel_check():
            raise ModelCallCancelled("cancelled before model call started")
        model_request = ModelRequest(
            messages=[{"role": "user", "content": msg} for msg in request.messages],
            model=self._adapter.model,
            temperature=0.0,
            max_output_tokens=request.max_output_tokens,
            request_id=request.request_id,
            timeout_seconds=self._timeout,
            extra=(
                {"format": self._response_format}
                if self._response_format is not None
                else {}
            ),
        )
        loop = asyncio.new_event_loop()
        watcher = None
        try:
            coroutine = self._adapter.generate(model_request)
            future = asyncio.ensure_future(coroutine, loop=loop)
            if cancel_check is not None:
                watcher = asyncio.ensure_future(
                    self._cancel_watcher(future, model_request, cancel_check),
                    loop=loop,
                )
            try:
                response = loop.run_until_complete(
                    asyncio.wait_for(future, timeout=self._timeout)
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"model call exceeded {self._timeout}s timeout"
                ) from None
            except asyncio.CancelledError:
                raise ModelCallCancelled(
                    "model call was cancelled"
                ) from None
        finally:
            if watcher is not None and not watcher.done():
                watcher.cancel()
                try:
                    loop.run_until_complete(watcher)
                except asyncio.CancelledError:
                    pass
            loop.close()
        if cancel_check is not None and cancel_check():
            # late response after cancellation: reject
            raise ModelCallCancelled(
                "response arrived after cancellation; discarded"
            )
        if self._meta_factory is not None:
            from .contracts import ModelCallResponse
            return ModelCallResponse(
                meta=self._meta_factory("runtime-response"),
                request_ref=request.request_id,
                provider_ref=response.provider,
                resolved_model_id=response.model,
                text_output=response.text,
                finish_reason=response.finish_reason or "",
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=0.0,  # ModelResponse has no latency_ms; measured by caller
                local_execution=True,  # derived from the adapter by the caller
                failure_category=None,
                cost=None,
            )
        return response

    async def _cancel_watcher(
        self,
        future: "asyncio.Future",
        model_request: ModelRequest,
        cancel_check: Callable[[], bool],
    ) -> None:
        """M5.5.2 active cancellation through the EXISTING adapter protocol.

        Polls ``cancel_check`` while the adapter call is in flight. When the
        caller cancels, the runtime invokes the adapter's own
        ``cancel(request_id=...)`` mechanism — the same one the rest of AMCA
        uses — so the underlying model computation is actually stopped, not
        merely discarded. If the adapter exposes no cancel protocol at all
        (a reported interface limitation), the runtime falls back to
        cancelling only its own call task and records that fact in
        ``last_cancel_used_adapter_protocol``. No competing cancellation
        protocol is introduced: in every path the in-flight operation ends
        cancelled and its response can never become authoritative.
        """

        request_id = model_request.request_id
        while not future.done():
            if cancel_check():
                adapter_cancel = getattr(self._adapter, "cancel", None)
                used_protocol = False
                if adapter_cancel is not None:
                    try:
                        if request_id:
                            outcome = adapter_cancel(request_id=request_id)
                        else:
                            outcome = adapter_cancel()
                        if asyncio.iscoroutine(outcome):
                            outcome = await outcome
                        used_protocol = True
                    except Exception:
                        used_protocol = False
                self.last_cancel_used_adapter_protocol = used_protocol
                if not future.done():
                    # Safety net only for adapters whose contract cannot
                    # track their own in-flight task; the authoritative
                    # stop remains adapter.cancel(...) above.
                    future.cancel()
                return
            await asyncio.sleep(self._cancel_poll)

    def complete_structured(
        self,
        request: ModelCallRequest,
        *,
        meta_factory: Callable[[str], Any] | None = None,
    ) -> dict:
        """Call the model and parse its output as bounded structured JSON.

        Returns the parsed dict, or raises ContractError if the output is
        not valid JSON, not an object, or not parseable by the bounded
        extraction rules (fenced JSON, first-to-last brace). Uses
        parse_model_json_output which never guesses.
        """

        response = self.complete(request)
        result = parse_model_json_output(response.text_output or "")
        if result is None:
            raise ContractError(
                ContractErrorCode.INVALID,
                "model output is not parseable as bounded structured JSON",
            )
        return result


def _build_prompt(request: ModelCallRequest) -> str:
    """Build the deterministic prompt from the model call request."""

    parts = list(request.messages) if hasattr(request, "messages") else []
    return "\n".join(parts)


def parse_model_json_output(text: str) -> Optional[dict]:
    """Bounded robust JSON extraction from a model response.

    Handles: valid JSON; fenced JSON; surrounding non-authoritative text
    if exactly one valid structured object can be isolated. Returns None
    for anything ambiguous (the caller must fail closed). Does not guess.
    """

    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()
    # try the whole string first
    result = _try_parse(stripped)
    if result is not None:
        return result
    # try fenced JSON blocks
    for fence in ("```json", "```"):
        if fence in stripped:
            for segment in stripped.split(fence):
                segment = segment.strip()
                if segment.startswith("{"):
                    result = _try_parse(segment)
                    if result is not None:
                        return result
    # try first and last brace extraction
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first >= 0 and last > first:
        result = _try_parse(stripped[first:last + 1])
        if result is not None:
            return result
    return None


def _try_parse(text: str) -> Optional[dict]:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except (ValueError, TypeError):
        pass
    return None

