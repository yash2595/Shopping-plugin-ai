"""Opt-in live text dialogue. Requires an endpoint/model and optional server API key.

No browser adapter is connected. Invoking this script may incur provider charges.
"""

import asyncio
from uuid import uuid4

from shopping_ai import ConversationTurn, Principal
from shopping_ai.providers.chat import ChatSettings
from shopping_ai.runtime import TextEvent, live_runtime


async def main():
    # Secrets are read explicitly; imports never load .env files or make requests.
    async with live_runtime(ChatSettings.from_env()) as assistant:
        # CLI-local demonstration identity, not a substitute for backend auth.
        principal = Principal(owner_id="local-demo", session_id=uuid4().hex)
        reply = await assistant.start(principal)
        print(reply.reply.message)
        print("Live model enabled. Browser execution unavailable. Type exit to finish.")
        while True:
            try:
                message = await asyncio.to_thread(input, "> ")
            except EOFError:
                break
            if message.strip().casefold() == "exit":
                break
            if not message.strip():
                continue
            reply = await assistant.handle(
                reply.state,
                principal,
                TextEvent(turn=ConversationTurn(request_id=uuid4().hex, message=message)),
                expected_version=reply.state.version,
            )
            print(reply.reply.message)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, OSError):
        raise SystemExit(
            "Check AI_CHAT configuration; no configuration values are displayed."
        ) from None
    except KeyboardInterrupt:
        pass
