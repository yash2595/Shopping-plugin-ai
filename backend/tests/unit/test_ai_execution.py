from threading import Event

import pytest
from app.ai.contracts import AIProviderError
from app.ai.execution import ProviderExecutor


def test_timeout_returns_while_provider_is_blocked_and_keeps_capacity_reserved():
    started, release, finished = Event(), Event(), Event()
    executor = ProviderExecutor(1)

    def blocked():
        started.set()
        release.wait(3)
        finished.set()
        return "late result"

    try:
        with pytest.raises(TimeoutError):
            executor.call(blocked, timeout=0.05)
        assert started.is_set()
        assert not finished.is_set()
        with pytest.raises(AIProviderError, match="PROVIDER_BUSY"):
            executor.call(lambda: "must not start", timeout=1)
    finally:
        release.set()
        executor.close()
    assert finished.wait(2)


def test_provider_exception_does_not_leak_a_capacity_slot():
    executor = ProviderExecutor(1)
    try:

        def fail():
            raise AIProviderError("SAFE_CODE")

        with pytest.raises(AIProviderError, match="SAFE_CODE"):
            executor.call(fail, timeout=1)
        assert executor.call(lambda: "ok", timeout=1) == "ok"
    finally:
        executor.close()
