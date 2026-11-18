"""Limit on how many predictions run at the same time."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio


class ServiceBusyError(Exception):
    """As many predictions as allowed are running and the queue behind them is full."""


class InferenceLimiter:
    """Let at most max_concurrent predictions run at once and up to max_queue more wait.

    Requests wait in the event loop, not in worker threads, so a queue of slow
    predictions does not hold the thread pool and /health keeps answering. A request
    that finds the queue full is refused at once instead of piling up.
    """

    def __init__(self, max_concurrent: int, max_queue: int) -> None:
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self.in_flight = 0  # running plus waiting
        self._slots = anyio.Semaphore(max_concurrent)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Wait for a free slot and hold it inside the block; raise ServiceBusyError
        when max_concurrent predictions run and max_queue more already wait."""
        if self.in_flight >= self.max_concurrent + self.max_queue:
            raise ServiceBusyError
        self.in_flight += 1
        try:
            async with self._slots:
                yield
        finally:
            self.in_flight -= 1
