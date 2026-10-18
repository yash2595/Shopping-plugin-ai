"""Public interface for the future backend adapter."""

from shopping_ai.config import AIConfig
from shopping_ai.engine import ShoppingAI, create_ai
from shopping_ai.models import AIRequest, AIResponse

__all__ = ["AIConfig", "AIRequest", "AIResponse", "ShoppingAI", "create_ai"]
