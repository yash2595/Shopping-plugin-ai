"""Offline simulated extension; never launches a browser or touches a real cart."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shopping_ai import AIRequest, Orchestrator, Principal, ShoppingAI
from shopping_ai.browser import BrowserResult, BrowserTarget, CartLine, CartSnapshot
from shopping_ai.providers import MockProvider


async def main():
    now = datetime.now(UTC)
    data = json.loads(Path(__file__).with_name("cart_proposal.json").read_text())
    data["product"]["observed_at"] = now.isoformat()
    data["authorization"]["expires_at"] = (now + timedelta(minutes=2)).isoformat()
    principal = Principal(owner_id="demo_user", session_id="demo_session")
    target = BrowserTarget(tab_id=1, document_id="doc_1", active=True, window_focused=True)
    orchestrator = Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now))
    state = orchestrator.start(principal, AIRequest.model_validate(data), target)
    print("SIMULATION ONLY: no browser actions or real cart changes.")
    for quantity in (0, 1):
        state = await orchestrator.advance(state, principal, expected_version=state.version)
        action = state.pending
        print(f"Proposed visible tool: {action.tool.name}; action_id={action.action_id}")
        lines = (
            ()
            if quantity == 0
            else (CartLine(product_id="earbuds_42", variant_id="black", quantity=1),)
        )
        result = BrowserResult(
            action_id=action.action_id,
            run_id=state.run_id,
            session_id=principal.session_id,
            run_version=action.run_version,
            source_document_id=target.document_id,
            target=target,
            observed_at=now,
            outcome="observed",
            cart=CartSnapshot(observed_at=now, document_id=target.document_id, lines=lines),
        )
        state = orchestrator.accept_result(state, principal, result, expected_version=state.version)
    print(f"Simulated final state: {state.status}; {state.summary}")


if __name__ == "__main__":
    asyncio.run(main())
