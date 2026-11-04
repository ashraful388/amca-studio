"""In-process typed event distribution for the AMCA kernel."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

from .domain import CognitiveEvent, EventType


@dataclass(slots=True, eq=False)
class EventSubscription:
    """A filtered event queue returned by :meth:`EventBus.subscribe`.

    The wrapper intentionally mirrors the useful ``asyncio.Queue`` methods and
    is also an async iterator/context manager.  Leaving the context unsubscribes
    it, preventing abandoned subscriptions from retaining events forever.
    """

    _bus: EventBus
    _queue: asyncio.Queue[CognitiveEvent]
    event_types: frozenset[EventType] | None
    closed: bool = False

    async def get(self) -> CognitiveEvent:
        if self.closed and self._queue.empty():
            raise RuntimeError("event subscription is closed")
        return await self._queue.get()

    def get_nowait(self) -> CognitiveEvent:
        if self.closed and self._queue.empty():
            raise RuntimeError("event subscription is closed")
        return self._queue.get_nowait()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    def empty(self) -> bool:
        return self._queue.empty()

    def full(self) -> bool:
        return self._queue.full()

    def qsize(self) -> int:
        return self._queue.qsize()

    def close(self) -> None:
        self._bus.unsubscribe(self)

    def __aiter__(self) -> AsyncIterator[CognitiveEvent]:
        return self

    async def __anext__(self) -> CognitiveEvent:
        if self.closed and self._queue.empty():
            raise StopAsyncIteration
        return await self.get()

    async def __aenter__(self) -> EventSubscription:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()


class EventBus:
    """A fan-out asyncio event bus with optional type filters.

    ``publish`` uses backpressure: it completes after each matching subscriber
    queue accepts the event.  Use a non-zero default queue size to bound memory;
    a stalled subscriber will then intentionally slow publishers.
    """

    def __init__(self, *, queue_size: int = 0) -> None:
        if queue_size < 0:
            raise ValueError("queue_size must be non-negative")
        self._queue_size = queue_size
        self._subscriptions: set[EventSubscription] = set()
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(
        self,
        event_types: EventType | Iterable[EventType] | None = None,
        *,
        queue_size: int | None = None,
    ) -> EventSubscription:
        """Subscribe to all events or a selected set of event types."""

        if self._closed:
            raise RuntimeError("event bus is closed")
        size = self._queue_size if queue_size is None else queue_size
        if size < 0:
            raise ValueError("queue_size must be non-negative")
        if event_types is None:
            normalized = None
        elif isinstance(event_types, EventType):
            normalized = frozenset((event_types,))
        else:
            normalized = frozenset(EventType(item) for item in event_types)
        subscription = EventSubscription(
            self, asyncio.Queue(maxsize=size), normalized
        )
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        """Remove a subscription. Calling this more than once is harmless."""

        self._subscriptions.discard(subscription)
        subscription.closed = True

    async def publish(self, event: CognitiveEvent) -> int:
        """Fan an event out and return the number of matching subscribers."""

        if self._closed:
            raise RuntimeError("event bus is closed")
        if not isinstance(event, CognitiveEvent):
            raise TypeError("EventBus accepts CognitiveEvent instances only")
        matched = [
            subscription
            for subscription in tuple(self._subscriptions)
            if not subscription.closed
            and (
                subscription.event_types is None
                or event.event_type in subscription.event_types
            )
        ]
        if matched:
            await asyncio.gather(
                *(subscription._queue.put(event) for subscription in matched)
            )
        return len(matched)

    async def emit(
        self,
        event_type: EventType,
        *,
        source: str,
        payload: dict[str, object] | None = None,
        state_version: int = 0,
        **event_fields: object,
    ) -> CognitiveEvent:
        """Construct, publish, and return a typed event."""

        event = CognitiveEvent(
            event_type=event_type,
            source=source,
            payload=dict(payload or {}),
            state_version=state_version,
            **event_fields,
        )
        await self.publish(event)
        return event

    def close(self) -> None:
        """Reject future work and close every subscription."""

        self._closed = True
        for subscription in tuple(self._subscriptions):
            self.unsubscribe(subscription)

    async def __aenter__(self) -> EventBus:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()


__all__ = ["EventBus", "EventSubscription"]
