"""A provider proposes one decision; it does not execute tools or store state."""

from typing import Protocol

from shopping_ai.context import BuiltContext
from shopping_ai.models import ModelDecision


class ProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class DecisionProvider(Protocol):
    name: str

    async def decide(self, context: BuiltContext) -> ModelDecision: ...
