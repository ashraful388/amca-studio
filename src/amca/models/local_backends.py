"""Named adapters for common OpenAI-compatible local inference servers."""

from __future__ import annotations

from typing import Any

from .openai_compatible import OpenAICompatibleAdapter


class _NamedLocalAdapter(OpenAICompatibleAdapter):
    _provider_name = "local-openai-compatible"
    _default_base_url = ""

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        allow_remote: bool = False,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("provider", self._provider_name)
        kwargs.setdefault("allow_remote", allow_remote)
        super().__init__(
            model=model,
            base_url=base_url or self._default_base_url,
            **kwargs,
        )


class LlamaCppAdapter(_NamedLocalAdapter):
    """llama.cpp's OpenAI-compatible server (default port 8080)."""

    _provider_name = "llama.cpp"
    _default_base_url = "http://127.0.0.1:8080/v1"


class VLLMAdapter(_NamedLocalAdapter):
    """vLLM's OpenAI-compatible server (default port 8000)."""

    _provider_name = "vllm"
    _default_base_url = "http://127.0.0.1:8000/v1"


class SGLangAdapter(_NamedLocalAdapter):
    """SGLang's OpenAI-compatible server (documented port 30000)."""

    _provider_name = "sglang"
    _default_base_url = "http://127.0.0.1:30000/v1"


__all__ = ["LlamaCppAdapter", "SGLangAdapter", "VLLMAdapter"]
