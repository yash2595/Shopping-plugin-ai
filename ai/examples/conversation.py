"""Offline dialogue and simulated executor; no browser, audio or real cart effects."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shopping_ai import (
    Conversation,
    ConversationTurn,
    Orchestrator,
    Principal,
    ShoppingAI,
)
from shopping_ai.browser import BrowserResult, BrowserTarget, CartLine, CartSnapshot
from shopping_ai.models import MutationAuthorization, ProductSnapshot
from shopping_ai.providers import MockProvider


async def main():
    now = datetime.now(UTC)
    data = json.loads(Path(__file__).with_name("cart_proposal.json").read_text())
    data["product"]["observed_at"] = now.isoformat()
    data["authorization"].update(
        request_id="cart_turn", expires_at=(now + timedelta(minutes=2)).isoformat()
    )
    product = ProductSnapshot.model_validate(data["product"])
    grant = MutationAuthorization.model_validate(data["authorization"])
    principal = Principal(owner_id="demo_user", session_id="demo_session")
    target = BrowserTarget(tab_id=1, document_id="doc_1", active=True, window_focused=True)
    dialogue = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    print("SIMULATION ONLY: no API, browser, speech playback or real cart changes.")
    reply = dialogue.start(principal)
    print("Assistant:", reply.message)
    for turn in (
        ConversationTurn(request_id="category_turn", message="Gym ke liye earbuds chahiye"),
        ConversationTurn(request_id="budget_turn", message="2k"),
        ConversationTurn(request_id="question_turn", message="Iska price?", products=(product,)),
        ConversationTurn(
            request_id="cart_turn",
            message="Black wala cart mein daal do",
            target=target,
            authorization=grant,
            available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
        ),
    ):
        print("User:", turn.message)
        reply = await dialogue.respond(
            reply.state, principal, turn, expected_version=reply.state.version
        )
        print("Assistant:", reply.message)
    for quantity in (0, 1):
        action = reply.action
        print("Simulated visible action:", action.tool.name)
        lines = (
            ()
            if quantity == 0
            else (CartLine(product_id=product.product_id, variant_id="black", quantity=1),)
        )
        result = BrowserResult(
            action_id=action.action_id,
            run_id=action.run_id,
            session_id=principal.session_id,
            run_version=action.run_version,
            source_document_id=target.document_id,
            target=target,
            observed_at=now,
            outcome="observed",
            cart=CartSnapshot(observed_at=now, document_id=target.document_id, lines=lines),
        )
        reply = dialogue.accept_result(
            reply.state, principal, result, expected_version=reply.state.version
        )
        if reply.status == "working":
            reply = await dialogue.advance(
                reply.state, principal, expected_version=reply.state.version
            )
        print("Assistant:", reply.message)

    other = ProductSnapshot.model_validate(
        product.model_dump()
        | {
            "product_id": "earbuds_99",
            "snapshot_id": "comparison_snapshot",
            "document_id": "doc_2",
            "title": "Second Demo Earbuds",
            "price": "1299.00",
        }
    )
    turn = ConversationTurn(
        request_id="compare_turn", message="Compare first and second", products=(product, other)
    )
    reply = await dialogue.respond(
        reply.state, principal, turn, expected_version=reply.state.version
    )
    print("Simulated evidence comparison:", reply.message)


if __name__ == "__main__":
    asyncio.run(main())
