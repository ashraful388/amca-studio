"""Deterministic offline adapter for tests, demos, and benchmark fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import replace
from typing import TypeAlias

from .base import (
    ModelAdapter,
    ModelCapabilities,
    ModelProtocolError,
    ModelRequest,
    ModelResponse,
    StreamChunk,
)


Script: TypeAlias = str | ModelResponse | Sequence[str]


class ScriptedAdapter(ModelAdapter):
    """Returns one preconfigured script for each generate/stream invocation."""

    def __init__(
        self,
        scripts: Iterable[Script],
        *,
        adapter_id: str = "scripted",
        model: str = "scripted-model",
        provider: str = "scripted",
        repeat_last: bool = False,
        chunk_size: int | None = None,
        delay_seconds: float = 0.0,
        capabilities: ModelCapabilities | None = None,
        is_local: bool = False,
    ) -> None:
        if chunk_size is not None and chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        declared_capabilities = (
            replace(capabilities, is_local=is_local)
            if capabilities is not None
            else ModelCapabilities(
                supports_streaming=True,
                supports_tools=True,
                supports_json=True,
                context_window=None,
                is_local=is_local,
            )
        )
        super().__init__(
            adapter_id=adapter_id,
            provider=provider,
            model=model,
            capabilities=declared_capabilities,
        )
        self._scripts = tuple(scripts)
        if not self._scripts:
            raise ValueError("scripts must contain at least one response")
        self._repeat_last = repeat_last
        self._chunk_size = chunk_size
        self._delay_seconds = delay_seconds
        self._next_index = 0
        self._lock = asyncio.Lock()

    @property
    def calls_consumed(self) -> int:
        return self._next_index

    async def _take_script(self) -> Script:
        async with self._lock:
            if self._next_index < len(self._scripts):
                script = self._scripts[self._next_index]
                self._next_index += 1
                return script
            if self._repeat_last:
                self._next_index += 1
                return self._scripts[-1]
        raise ModelProtocolError("scripted adapter has no response remaining")

    def _response(self, script: Script, request: ModelRequest) -> ModelResponse:
        if isinstance(script, ModelResponse):
            usage = script.usage
            if usage.total_tokens == 0:
                usage = self._usage(
                    self.estimate_tokens(request), self.estimate_tokens(script.text)
                )
            return replace(
                script,
                model=script.model or self._resolved_model(request),
                provider=script.provider or self.provider,
                usage=usage,
            )
        text = script if isinstance(script, str) else "".join(script)
        usage = self._usage(self.estimate_tokens(request), self.estimate_tokens(text))
        return ModelResponse(
            text=text,
            model=self._resolved_model(request),
            provider=self.provider,
            usage=usage,
            finish_reason="stop",
        )

    def _chunks(self, script: Script) -> tuple[str, ...]:
        if isinstance(script, ModelResponse):
            text = script.text
        elif isinstance(script, str):
            text = script
        else:
            return tuple(script)
        if self._chunk_size is None:
            return (text,) if text else ()
        return tuple(
            text[index : index + self._chunk_size]
            for index in range(0, len(text), self._chunk_size)
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        async with self._call_scope(request):
            script = await self._take_script()
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            return self._response(script, request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        async with self._call_scope(request):
            script = await self._take_script()
            response = self._response(script, request)
            for delta in self._chunks(script):
                if self._delay_seconds:
                    await asyncio.sleep(self._delay_seconds)
                yield StreamChunk(
                    delta=delta,
                    model=response.model,
                    provider=response.provider,
                )
            yield StreamChunk(done=True, response=response)
