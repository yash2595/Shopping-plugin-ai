"""Optional core runtime composition. All providers are owned by app lifespan."""

from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

from shopping_ai.config import AIConfig
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market import Store
from shopping_ai.market_dialogue import OfflineDetailInterpreter
from shopping_ai.providers.chat import ChatProvider, ChatSettings
from shopping_ai.providers.mock import MockProvider
from shopping_ai.providers.speech import AudioService, SpeechProvider, SpeechSettings
from shopping_ai.runtime import create_runtime


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
        if settings.AI_RUNTIME_PROVIDER == "chat":
            decisions = await stack.enter_async_context(
                ChatProvider(
                    ChatSettings(
                        endpoint=settings.AI_CHAT_ENDPOINT,
                        model=settings.AI_CHAT_MODEL,
                        api_key=settings.AI_CHAT_API_KEY or None,
                    ),
                    config,
                )
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
