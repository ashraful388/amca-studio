"""Registry for configured AMCA model adapters."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType

from .base import ModelAdapter


class DuplicateAdapterError(ValueError):
    """Raised when two adapters claim the same stable adapter ID."""


class UnknownAdapterError(KeyError):
    """Raised when an adapter ID is not registered."""


class ModelRegistry:
    """In-memory registry enforcing globally unique configured adapter IDs."""

    def __init__(self, adapters: Iterable[ModelAdapter] = ()) -> None:
        self._adapters: dict[str, ModelAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ModelAdapter) -> ModelAdapter:
        if not isinstance(adapter, ModelAdapter):
            raise TypeError("adapter must implement ModelAdapter")
        if adapter.adapter_id in self._adapters:
            raise DuplicateAdapterError(
                f"adapter ID {adapter.adapter_id!r} is already registered"
            )
        self._adapters[adapter.adapter_id] = adapter
        return adapter

    def unregister(self, adapter_id: str) -> ModelAdapter:
        try:
            return self._adapters.pop(adapter_id)
        except KeyError as exc:
            raise UnknownAdapterError(adapter_id) from exc

    def get(self, adapter_id: str, default: ModelAdapter | None = None) -> ModelAdapter | None:
        return self._adapters.get(adapter_id, default)

    def require(self, adapter_id: str) -> ModelAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise UnknownAdapterError(adapter_id) from exc

    def snapshot(self) -> Mapping[str, ModelAdapter]:
        return MappingProxyType(dict(self._adapters))

    def ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def values(self) -> tuple[ModelAdapter, ...]:
        return tuple(self._adapters.values())

    def items(self) -> tuple[tuple[str, ModelAdapter], ...]:
        return tuple(self._adapters.items())

    def __getitem__(self, adapter_id: str) -> ModelAdapter:
        return self.require(adapter_id)

    def __contains__(self, adapter_id: object) -> bool:
        return adapter_id in self._adapters

    def __iter__(self) -> Iterator[str]:
        return iter(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)

