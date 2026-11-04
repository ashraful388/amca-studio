"""Fixed-per-request OpenAI-compatible cloud adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from .openai_compatible import OpenAICompatibleAdapter


class FixedPriceOpenAICompatibleAdapter(OpenAICompatibleAdapter):
    def __init__(self, *, price_per_request_usd: float, **kwargs: Any) -> None:
        if not math.isfinite(price_per_request_usd) or price_per_request_usd <= 0:
            raise ValueError("price_per_request_usd must be finite and positive")
        self._price_per_request_usd = float(price_per_request_usd)
        super().__init__(**kwargs)

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


__all__ = ["FixedPriceOpenAICompatibleAdapter"]
