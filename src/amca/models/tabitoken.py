"""Fixed-price Tabitoken OpenAI-compatible adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from amca.credentials import ProviderCredentialResolver

from .openai_compatible import OpenAICompatibleAdapter


class TabitokenAdapter(OpenAICompatibleAdapter):
    """Use one model-scoped key against Tabitoken's chat-completions API.

    Tabitoken advertises these models at a fixed price per request. Returning
    that exact price from ``estimate_cost`` lets the kernel reserve the complete
    multi-model batch before any credential-bearing transmission.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str,
        price_per_request_usd: float,
        base_url: str = "https://tabitoken.com/v1",
        adapter_id: str | None = None,
        timeout: float = 120.0,
        urlopen_fn: Any = None,
        credential_resolver: ProviderCredentialResolver | None = None,
        credential_provider: str | None = None,
    ) -> None:
        if not math.isfinite(price_per_request_usd) or price_per_request_usd <= 0:
            raise ValueError("price_per_request_usd must be finite and positive")
        self._price_per_request_usd = float(price_per_request_usd)
        super().__init__(
            model=model,
            base_url=base_url,
            adapter_id=adapter_id or f"tabitoken:{model}",
            provider="tabitoken",
            api_key_env=api_key_env,
            timeout=timeout,
            allow_remote=True,
            default_headers={
                "Accept": "application/json",
                "User-Agent": "AMCA/0.1",
            },
            urlopen_fn=urlopen_fn,
            credential_resolver=credential_resolver,
            credential_provider=credential_provider or f"tabitoken.{model}",
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int = 0) -> float:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        return self._price_per_request_usd

    def provider_metadata(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                **dict(super().provider_metadata()),
                "pricing_basis": "fixed_per_request",
                "price_per_request_usd": self._price_per_request_usd,
            }
        )
