"""Offline shared-runtime demonstration. Simulated offers; no browser/network/audio."""

import asyncio
from datetime import UTC, datetime

from shopping_ai import ConversationTurn, Principal
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market import Offer, SearchReceipt, Store
from shopping_ai.market_dialogue import OfflineDetailInterpreter
from shopping_ai.providers import MockProvider
from shopping_ai.runtime import MarketEvent, TextEvent, create_runtime


async def main():
    now = datetime.now(UTC)
    stores = tuple(
        Store(
            store_id=name,
            origin=f"https://{name}.example",
            search_path="/search",
            query_parameter="q",
        )
        for name in ("store-a", "store-b")
    )
    assistant = create_runtime(
        MockProvider(),
        interpreter=BasicInterpreter(),
        detail_interpreter=OfflineDetailInterpreter(),
        stores=stores,
        clock=lambda: now,
    )
    principal = Principal(owner_id="user1", session_id="session1")
    output = await assistant.start(principal, language="en")
    for message in (
        "find cheapest across websites",
        "Demo",
        "Phone",
        "storage=128GB, colour=black",
        "new",
        "201001",
        "confirm",
    ):
        output = await assistant.handle(
            output.state,
            principal,
            TextEvent(
                turn=ConversationTurn(
                    request_id=f"turn{output.state.version}",
                    message=message,
                )
            ),
            expected_version=output.state.version,
        )
        print("Assistant:", output.reply.message)
    tasks = output.conversation_reply.search_tasks
    for index, task in enumerate(tasks):
        market = output.state.active_market
        print("Visible-browser task (not executed):", task.url)
        receipt = SearchReceipt(
            search_id=market.search_id,
            owner_id=market.owner_id,
            session_id=market.session_id,
            task_id=task.task_id,
            delivery_context=market.delivery_context,
            status="complete",
            offers=(
                Offer(
                    offer_id=f"offer{index}",
                    snapshot_id=f"snapshot{index}",
                    identity=market.identity,
                    url=task.store.origin + "/product/demo",
                    seller="Demo seller",
                    observed_at=now,
                    in_stock=True,
                    price="999" if index == 0 else "1049",
                    currency="INR",
                    shipping="100" if index == 0 else "0",
                    extra_tax_and_fees="0",
                ),
            ),
        )
        output = await assistant.handle(
            output.state,
            principal,
            MarketEvent(receipt=receipt),
            expected_version=output.state.version,
        )
    result = output.conversation_reply.market_result
    print(result.message)
    for item in result.ranked:
        print(item.store_id, item.total, item.offer.url)
    for message in ("shipping breakdown", "coverage"):
        output = await assistant.handle(
            output.state,
            principal,
            TextEvent(
                turn=ConversationTurn(
                    request_id=f"question{output.state.version}",
                    message=message,
                )
            ),
            expected_version=output.state.version,
        )
        print("Follow-up:", output.conversation_reply.message)


if __name__ == "__main__":
    asyncio.run(main())
