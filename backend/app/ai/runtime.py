"""Optional core runtime composition. All providers are owned by app lifespan."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from shopping_ai.config import AIConfig
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market import Store
from shopping_ai.market_dialogue import OfflineDetailInterpreter
from shopping_ai.providers.base import ProviderError
from shopping_ai.providers.chat import ChatProvider, ChatSettings
from shopping_ai.providers.mock import MockProvider
from shopping_ai.providers.speech import AudioService, SpeechProvider, SpeechSettings
from shopping_ai.runtime import create_runtime

inference_fallback = ContextVar("inference_fallback", default=None)


class LocalFirstProvider:
    """Inference-only degradation; fallback never dispatches or executes an action."""

    name = "local_first"

    def __init__(
        self, local, decisions, interpreter, details, *, timeout, fallback_kind="deterministic"
    ):
        self.local = local
        self.decisions, self.interpreter, self.details = decisions, interpreter, details
        self.timeout = timeout
        self.fallback_kind = fallback_kind

    async def _call(self, operation, fallback, *args):
        try:
            async with asyncio.timeout(self.timeout):
                return await getattr(self.local, operation)(*args)
        except (ProviderError, TimeoutError):
            inference_fallback.set(self.fallback_kind)
            return await getattr(fallback, operation)(*args)

    async def plan_navigation(self, request):
        from shopping_ai.navigation import RulesBrowserPlanner
        fallback = self.decisions if self.fallback_kind == "cloud" else RulesBrowserPlanner()
        return await self._call("plan_navigation", fallback, request)

    async def decide(self, context):
        return await self._call("decide", self.decisions, context)

    async def interpret(self, message, awaiting):
        return await self._call("interpret", self.interpreter, message, awaiting)

    async def extract(self, message, draft):
        return await self._call("extract", self.details, message, draft)


@asynccontextmanager
async def configured_runtime(settings):
    for origin in settings.AI_BROWSER_ORIGINS:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError("AI_BROWSER_ORIGINS requires plain trusted HTTPS origins")
    stores = tuple(Store.model_validate(store) for store in settings.AI_SEARCH_STORES)
    config = AIConfig(
        timeout_seconds=float(settings.AI_CORE_TIMEOUT_SECONDS),
        max_concurrent_calls=settings.AI_MAX_CONCURRENT_CALLS,
        max_steps=settings.AI_MAX_STEPS,
        max_snapshot_age_seconds=settings.AI_MAX_SNAPSHOT_AGE_SECONDS,
        search_timeout_seconds=settings.AI_SEARCH_TIMEOUT_SECONDS,
    )
    async with AsyncExitStack() as stack:
        decisions, interpreter, details = (
            MockProvider(),
            BasicInterpreter(),
            OfflineDetailInterpreter(),
        )
        if settings.AI_RUNTIME_PROVIDER == "chat" or (
            settings.AI_RUNTIME_PROVIDER == "local" and settings.AI_LOCAL_FALLBACK == "cloud"
        ):
            decisions = await stack.enter_async_context(
                ChatProvider(
                    ChatSettings(
                        endpoint=settings.AI_CHAT_ENDPOINT,
                        model=settings.AI_CHAT_MODEL,
                        api_key=settings.AI_CHAT_API_KEY or None,
                    ),
                    config,
                    navigation_model=settings.AI_CHAT_NAVIGATION_MODEL or None,
                )
            )
            interpreter = details = decisions
        if settings.AI_RUNTIME_PROVIDER == "local":
            local = await stack.enter_async_context(
                ChatProvider(
                    ChatSettings(
                        local_only=True,
                        endpoint=settings.AI_LOCAL_ENDPOINT,
                        model=settings.AI_LOCAL_MODEL,
                        token_parameter="max_tokens",
                    ),
                    config.model_copy(
                        update={"timeout_seconds": float(settings.AI_LOCAL_TIMEOUT_SECONDS)}
                    ),
                    navigation_model=settings.AI_LOCAL_NAVIGATION_MODEL or None,
                )
            )
            decisions = LocalFirstProvider(
                local,
                decisions,
                interpreter,
                details,
                timeout=settings.AI_LOCAL_TIMEOUT_SECONDS,
                fallback_kind=settings.AI_LOCAL_FALLBACK,
            )
            interpreter = details = decisions
        speech = None
        if settings.AI_STT_ENDPOINT or settings.AI_TTS_ENDPOINT:
            speech = await stack.enter_async_context(
                SpeechProvider(
                    SpeechSettings(
                        stt=AudioService(
                            endpoint=settings.AI_STT_ENDPOINT,
                            model=settings.AI_STT_MODEL,
                            api_key=settings.AI_STT_API_KEY or None,
                        ),
                        tts=AudioService(
                            endpoint=settings.AI_TTS_ENDPOINT,
                            model=settings.AI_TTS_MODEL,
                            api_key=settings.AI_TTS_API_KEY or None,
                        ),
                        voice=settings.AI_TTS_VOICE,
                        output_format=settings.AI_TTS_FORMAT,
                    ),
                    config,
                )
            )
        yield create_runtime(
            decisions,
            interpreter=interpreter,
            detail_interpreter=details,
            transcriber=speech,
            synthesizer=speech,
            config=config,
            stores=stores,
        )
