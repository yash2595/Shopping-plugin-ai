"""Deterministic checks on proposals. The executor must check fresh state again."""

from datetime import datetime

from shopping_ai.context import BuiltContext
from shopping_ai.models import AIRequest, ToolProposal


class PolicyViolation(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_tool(
    proposal: ToolProposal, request: AIRequest, context: BuiltContext, now: datetime
) -> None:
    if proposal.name not in request.available_tools:
        raise PolicyViolation("TOOL_UNAVAILABLE", "This page does not support that tool.")
    if proposal.name in {"read_product", "read_cart"}:
        return
    product = request.product
    if not product or not context.fresh:
        raise PolicyViolation("STALE_OBSERVATION", "Refresh the product before changing the page.")
    if proposal.product_id != product.product_id:
        raise PolicyViolation(
            "WRONG_PRODUCT", "The proposed product differs from the selected product."
        )
    if proposal.variant_id not in {v.variant_id for v in product.variants}:
        raise PolicyViolation("UNKNOWN_VARIANT", "Select an observed variant.")
    grant = request.authorization
    if not grant:
        raise PolicyViolation(
            "AUTHORIZATION_REQUIRED", "Confirm the exact item and quantity first."
        )
    if grant.request_id != request.request_id or proposal.name not in grant.allowed_tools:
        raise PolicyViolation(
            "AUTHORIZATION_SCOPE", "This grant does not permit this request/tool."
        )
    if grant.expires_at <= now:
        raise PolicyViolation(
            "AUTHORIZATION_EXPIRED", "Confirm the action again before proceeding."
        )
    if (grant.product_id, grant.document_id, grant.variant_id) != (
        product.product_id,
        product.document_id,
        proposal.variant_id,
    ):
        raise PolicyViolation(
            "TARGET_MISMATCH", "The action no longer matches the authorized page/item."
        )
    if proposal.name == "select_variant":
        return  # The executor must re-read the new variant offer before any cart proposal.
    if (proposal.quantity, proposal.mode) != (grant.quantity, grant.mode):
        raise PolicyViolation(
            "QUANTITY_MISMATCH", "The quantity or operation differs from user intent."
        )
    if product.selected_variant_id != proposal.variant_id:
        raise PolicyViolation(
            "VARIANT_NOT_SELECTED", "Select and re-read the intended variant first."
        )
    if product.in_stock is not True:
        raise PolicyViolation(
            "STOCK_UNCONFIRMED", "Stock is unavailable or has not been confirmed."
        )
    if product.price is None or product.currency != grant.currency:
        raise PolicyViolation("PRICE_UNCONFIRMED", "Re-read the price and currency.")
    if product.price > grant.max_unit_price:
        raise PolicyViolation(
            "PRICE_LIMIT", "The observed unit price exceeds the authorized limit."
        )
