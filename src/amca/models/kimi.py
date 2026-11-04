"""Kimi/Moonshot adapter for its OpenAI-compatible API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from amca.credentials import ProviderCredentialResolver

from .base import ModelCapabilities, ModelConfigurationError
from .openai_compatible import OpenAICompatibleAdapter
from .base import ModelRequest


class KimiAdapter(OpenAICompatibleAdapter):
    """Cloud adapter for Kimi using Moonshot's chat-completions endpoint."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.moonshot.ai/v1",
        adapter_id: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "MOONSHOT_API_KEY",
        timeout: float = 120.0,
        capabilities: ModelCapabilities | None = None,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
        default_headers: Mapping[str, str] | None = None,
        urlopen_fn: Any = None,
        credential_resolver: ProviderCredentialResolver | None = None,
        credential_provider: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            adapter_id=adapter_id or f"kimi:{model}",
            provider="kimi",
            api_key=api_key,
            api_key_env=api_key_env,
            timeout=timeout,
            allow_remote=True,
            capabilities=capabilities
            or ModelCapabilities(
                supports_streaming=True,
                supports_tools=True,
                supports_json=True,
                is_local=False,
                context_window=None,
            ),
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            default_headers=default_headers,
            urlopen_fn=urlopen_fn,
            credential_resolver=credential_resolver,
            credential_provider=credential_provider,
        )

    def _resolve_api_key(self) -> str:
        api_key = super()._resolve_api_key()
        if not api_key:
            raise ModelConfigurationError(
                f"Kimi credential is unavailable; set {self._api_key_env} "
                "or pass api_key"
            )
        return api_key

    def _payload(
        self,
        request: ModelRequest,
        *,
        stream: bool,
        transmits: bool | None = None,
    ) -> dict[str, Any]:
        """Apply Kimi's model-specific sampling requirements at the boundary."""

        payload = super()._payload(request, stream=stream, transmits=transmits)
        if "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        model = self._resolved_model(request).casefold()
        if model.startswith(("kimi-k2.6", "kimi-k2.5")):
            thinking = payload.get("thinking")
            disabled = (
                isinstance(thinking, Mapping)
                and thinking.get("type") == "disabled"
            )
            payload["temperature"] = 0.6 if disabled else 1.0
        elif model.startswith("kimi-k2-thinking"):
            payload["temperature"] = 1.0
        return payload
