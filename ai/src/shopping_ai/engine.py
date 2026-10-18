"""One bounded AI step; no API server, DB, browser executor or background task."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError

from shopping_ai.config import AIConfig
from shopping_ai.context import PROMPT_VERSION, ContextTooLarge, build_context
from shopping_ai.models import AIRequest, AIResponse, ModelDecision
from shopping_ai.policy import PolicyViolation, validate_tool
from shopping_ai.providers.base import DecisionProvider, ProviderError
from shopping_ai.providers.mock import MockProvider


class ShoppingAI:
    def __init__(
        self,
        provider: DecisionProvider,
        config: AIConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.provider = provider
        self.config = config or AIConfig()
        self.clock = clock or (lambda: datetime.now(UTC))
        self._capacity = asyncio.Semaphore(self.config.max_concurrent_calls)

    async def decide(self, request: AIRequest) -> AIResponse:
        # Frozen models still contain mutable collections. Validate a private copy
        # before awaiting a provider so later caller edits cannot change this step.
        request = AIRequest.model_validate(request.model_dump(warnings=False))

        def reply(status: str, message: str, **kwargs) -> AIResponse:
            return AIResponse(
                request_id=request.request_id,
                status=status,
                message=message,
                provider=self.provider.name,
                prompt_version=PROMPT_VERSION,
                **kwargs,
            )

        if request.stop_requested:
            return reply(
                "cancelled", "Further actions stopped. In-flight actions need reconciliation."
            )
        if request.step_count >= self.config.max_steps:
            return reply(
                "blocked",
                "Task step budget exhausted. Review the task before continuing.",
                code="STEP_LIMIT",
            )
        try:
            context = build_context(request, self.config, self.clock())
            try:
                async with asyncio.timeout(self.config.capacity_wait_seconds):
                    await self._capacity.acquire()
            except TimeoutError:
                return reply(
                    "error", "AI capacity is busy. Retry later.", code="AI_CAPACITY", retryable=True
                )
            try:
                async with asyncio.timeout(self.config.timeout_seconds):
                    raw = await self.provider.decide(context)
            finally:
                self._capacity.release()
            # Provider implementations are an untrusted boundary; revalidate their output.
            decision = ModelDecision.model_validate(
                raw.model_dump(warnings=False) if isinstance(raw, ModelDecision) else raw
            )
            if decision.kind == "clarification":
                return reply("needs_input", decision.question)
            if decision.kind == "answer":
                # A model can finish after the snapshot freshness window has elapsed.
                latest_context = build_context(request, self.config, self.clock())
                facts = {f.fact_id: f for f in latest_context.facts}
                if any(key not in facts for key in decision.fact_ids):
                    return reply(
                        "blocked",
                        "The answer references missing or stale evidence.",
                        code="INVALID_EVIDENCE",
                    )
                selected = [facts[key] for key in decision.fact_ids]
                # Output is rendered from exact observed values, not unchecked model prose.
                text = "\n".join(f"{f.key}: {f.value}" for f in selected)
                return reply("answered", text, facts=selected)
            latest_context = build_context(request, self.config, self.clock())
            validate_tool(decision.tool, request, latest_context, self.clock())
            return reply(
                "tool_proposed",
                "Validated proposal only; no browser action has executed.",
                tool=decision.tool,
                expected_document_id=request.product.document_id if request.product else None,
            )
        except ContextTooLarge:
            return reply(
                "blocked",
                "Relevant evidence is too large. Send a smaller product snapshot.",
                code="CONTEXT_LIMIT",
            )
        except PolicyViolation as exc:
            return reply("blocked", str(exc), code=exc.code)
        except TimeoutError:
            return reply(
                "error",
                "Model timed out. No browser action was executed.",
                code="MODEL_TIMEOUT",
                retryable=True,
            )
        except (ValidationError, ValueError, TypeError):
            return reply(
                "error",
                "Model returned an invalid decision. No tool was dispatched.",
                code="INVALID_MODEL_OUTPUT",
            )
        except ProviderError as exc:
            return reply(
                "error",
                "Model provider could not return a decision.",
                code=exc.code,
                retryable=exc.retryable,
            )


def create_ai(config: AIConfig | None = None) -> ShoppingAI:
    config = config or AIConfig.from_env()
    if config.provider == "gemini":
        from shopping_ai.providers.gemini import GeminiProvider

        return ShoppingAI(GeminiProvider(config), config)
    return ShoppingAI(MockProvider(), config)
