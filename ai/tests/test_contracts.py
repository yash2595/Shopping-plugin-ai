import pytest
from pydantic import ValidationError

from shopping_ai.models import AIRequest, ModelDecision, ToolProposal


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "answer", "fact_ids": []},
        {"kind": "answer", "fact_ids": ["x", "x"]},
        {"kind": "answer", "fact_ids": ["x"], "question": "also ask?"},
        {"kind": "tool"},
        {"kind": "clarification", "question": ""},
        {"kind": "tool", "tool": {"name": "checkout"}},
    ],
)
def test_invalid_decisions_rejected(payload):
    with pytest.raises(ValidationError):
        ModelDecision.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "read_product", "product_id": "arbitrary"},
        {"name": "select_variant", "product_id": "p42"},
        {"name": "add_to_cart", "product_id": "p42", "variant_id": "black"},
        {"name": "read_cart", "javascript": "alert(1)"},
        {
            "name": "add_to_cart",
            "product_id": "p42",
            "variant_id": "black",
            "quantity": True,
            "mode": "ensure_quantity",
        },
        {
            "name": "add_to_cart",
            "product_id": "p42",
            "variant_id": "black",
            "quantity": 0,
            "mode": "ensure_quantity",
        },
    ],
)
def test_invalid_tool_arguments_rejected(payload):
    with pytest.raises(ValidationError):
        ToolProposal.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"price": "nan"},
        {"price": "-1.00"},
        {"currency": None},
        {"observed_at": "2026-10-18T16:30:00"},
        {"selected_variant_id": "unknown"},
        {"cookies": "do-not-upload"},
    ],
)
def test_invalid_product_data_rejected(product, changes):
    with pytest.raises(ValidationError):
        type(product).model_validate(product.model_dump() | changes)


def test_blank_message_rejected():
    with pytest.raises(ValidationError):
        AIRequest(request_id="r1", message="   ")
