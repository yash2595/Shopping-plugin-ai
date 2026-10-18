"""Offline cross-store comparison using simulated browser receipts, not live prices."""

import asyncio
from datetime import UTC, datetime

from shopping_ai import Conversation, ConversationTurn, Orchestrator, Principal, ShoppingAI
from shopping_ai.market import (
    Offer,
    SearchReceipt,
    Store,
)
from shopping_ai.providers import MockProvider

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
conversation = Conversation(
    Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)),
    stores=stores,
)
principal = Principal(owner_id="user1", session_id="session1")
reply = conversation.start(principal, language="en")
for message in (
    "find cheapest across websites",
    "Demo",
    "Phone",
    "storage=128GB, colour=black",
    "new",
    "201001",
    "confirm",
):
    reply = asyncio.run(
        conversation.respond(
            reply.state,
            principal,
            ConversationTurn(request_id=f"turn{reply.state.version}", message=message),
            expected_version=reply.state.version,
        )
    )
    print("Assistant:", reply.message)
identity = reply.state.active_market.identity
for index, task in enumerate(reply.search_tasks):
    state = reply.state.active_market
    print("Visible-browser task (not executed):", task.url)
    receipt = SearchReceipt(
        search_id=state.search_id,
        owner_id=state.owner_id,
        session_id=state.session_id,
        task_id=task.task_id,
        delivery_context=state.delivery_context,
        status="complete",
        offers=(
            Offer(
                offer_id=f"offer{index}",
                snapshot_id=f"snapshot{index}",
                identity=identity,
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
    reply = conversation.accept_market_receipt(
        reply.state,
        principal,
        receipt,
        expected_version=reply.state.version,
    )
result = reply.market_result
print(result.message)
for item in result.ranked:
    print(item.store_id, item.total, item.offer.url)
