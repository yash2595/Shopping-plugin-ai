"""Bounded, process-local provider admission shared by chat and review requests."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import BoundedSemaphore
from typing import Callable, TypeVar

from app.ai.contracts import AIProviderError
from app.config import get_settings

T = TypeVar("T")


class ProviderExecutor:
    def __init__(self, capacity: int):
        self._slots = BoundedSemaphore(capacity)
        self._pool = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="ai-provider")

    def call(self, operation: Callable[[], T], *, timeout: float) -> T:
        if not self._slots.acquire(blocking=False):
            raise AIProviderError("PROVIDER_BUSY")
        try:
            future = self._pool.submit(operation)
        except Exception:
            self._slots.release()
            raise
        # A running thread cannot be killed. Retain its slot until it really exits;
        # otherwise repeated timeouts could create unbounded provider concurrency.
        future.add_done_callback(lambda _: self._slots.release())
        try:
            return future.result(timeout=timeout)
        finally:
            future.cancel()

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


@lru_cache
def get_provider_executor() -> ProviderExecutor:
    return ProviderExecutor(get_settings().AI_MAX_CONCURRENT_CALLS)
