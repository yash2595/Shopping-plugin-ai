"""Deterministic offline demo, NOT a language model or production intent classifier."""

import json

from shopping_ai.context import BuiltContext
from shopping_ai.models import ModelDecision, ToolProposal


class MockProvider:
    name = "mock"

    async def decide(self, context: BuiltContext) -> ModelDecision:
        data = json.loads(context.payload)
        message = data["message"].lower()
        product = data["product"]
        if any(word in message for word in ("cart", "select", "choose", "daal")):
            grant = data["authorization"]
            if not grant:
                return ModelDecision(
                    kind="clarification", question="Exact variant aur quantity confirm karo."
                )
            if not product or not product["observation_fresh"]:
                return self._read_or_ask(data)
            target = grant["variant_id"]
            if (
                any(word in message for word in ("select", "choose"))
                or product["selected_variant_id"] != target
            ):
                tool = ToolProposal(
                    name="select_variant", product_id=grant["product_id"], variant_id=target
                )
            else:
                tool = ToolProposal(
                    name="add_to_cart",
                    product_id=grant["product_id"],
                    variant_id=target,
                    quantity=grant["quantity"],
                    mode=grant["mode"],
                )
            return ModelDecision(kind="tool", tool=tool)
        if not product or not product["observation_fresh"]:
            return self._read_or_ask(data)
        facts = data["facts"]
        keys: set[str] = set()
        for words, key in [
            (("price", "cost", "daam"), "price"),
            (("colour", "color", "variant"), "selected_variant"),
            (("stock", "available"), "stock"),
            (("name", "title"), "title"),
        ]:
            if any(word in message for word in words):
                keys.add(key)
        selected = [
            f["fact_id"]
            for f in facts
            if f["key"] in keys
            or (
                f["key"].startswith("spec:")
                and any(term in message for term in f["value"].split(":", 1)[0].lower().split())
            )
        ][:10]
        if selected:
            return ModelDecision(kind="answer", fact_ids=selected)
        return ModelDecision(
            kind="clarification",
            question="Is detail ka evidence nahi hai. Price, stock ya specs poochho.",
        )

    @staticmethod
    def _read_or_ask(data: dict) -> ModelDecision:
        if "read_product" in data["available_tools"]:
            return ModelDecision(kind="tool", tool=ToolProposal(name="read_product"))
        return ModelDecision(
            kind="clarification", question="Current product ka fresh snapshot bhejo."
        )
