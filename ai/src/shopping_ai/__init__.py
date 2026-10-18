"""Public interface for the future backend adapter."""

from shopping_ai.config import AIConfig
from shopping_ai.engine import ShoppingAI, create_ai
from shopping_ai.models import AIRequest, AIResponse
from shopping_ai.orchestrator import Orchestrator, Principal, RunState

__all__ = [
    "AIConfig",
    "AIRequest",
    "AIResponse",
    "ShoppingAI",
    "create_ai",
    "Orchestrator",
    "Principal",
    "RunState",
]
