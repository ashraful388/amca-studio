"""Provider-neutral model access for AMCA.

Importing this package never imports optional cloud-provider SDKs and never
reads API-key environment variables.
"""

from .anthropic import AnthropicAdapter
from .base import (
    ModelAdapter,
    ModelAuthenticationError,
    ModelCapabilities,
    ModelCancelledError,
    ModelConfigurationError,
    ModelError,
    ModelInvalidRequestError,
    ModelProtocolError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelRequest,
    ModelResponse,
    ModelTimeoutError,
    ModelUnavailableError,
    StreamChunk,
    TokenUsage,
)
from .gemini import GeminiAdapter
from .fixed_price import FixedPriceOpenAICompatibleAdapter
from .kimi import KimiAdapter
from .local_backends import LlamaCppAdapter, SGLangAdapter, VLLMAdapter
from .ollama import OllamaAdapter
from .openai import OpenAIAdapter
from .openai_compatible import (
    LocalOpenAICompatibleAdapter,
    OpenAICompatibleAdapter,
)
from .registry import DuplicateAdapterError, ModelRegistry, UnknownAdapterError
from .scripted import ScriptedAdapter
from .tabitoken import TabitokenAdapter
from .tool_protocol import (
    ToolDefinition,
    ToolCall,
    ToolResult,
    ToolResultState,
    effective_tool_support,
)

__all__ = [
    "AnthropicAdapter",
    "DuplicateAdapterError",
    "GeminiAdapter",
    "FixedPriceOpenAICompatibleAdapter",
    "KimiAdapter",
    "LlamaCppAdapter",
    "LocalOpenAICompatibleAdapter",
    "ModelAdapter",
    "ModelAuthenticationError",
    "ModelCapabilities",
    "ModelCancelledError",
    "ModelConfigurationError",
    "ModelError",
    "ModelInvalidRequestError",
    "ModelProtocolError",
    "ModelProviderUnavailableError",
    "ModelRateLimitError",
    "ModelRegistry",
    "ModelRequest",
    "ModelResponse",
    "ModelTimeoutError",
    "ModelUnavailableError",
    "OllamaAdapter",
    "OpenAIAdapter",
    "OpenAICompatibleAdapter",
    "ScriptedAdapter",
    "SGLangAdapter",
    "StreamChunk",
    "TokenUsage",
    "TabitokenAdapter",
    "ToolDefinition",
    "ToolCall",
    "ToolResult",
    "ToolResultState",
    "effective_tool_support",
    "UnknownAdapterError",
    "VLLMAdapter",
]
