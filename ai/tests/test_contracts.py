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


@pytest.mark.parametrize("value", ["true", "false", 0, 1])
def test_stock_requires_actual_boolean(product, value):
    with pytest.raises(ValidationError):
        type(product).model_validate(product.model_dump() | {"in_stock": value})


def test_whitespace_clarification_is_rejected():
    with pytest.raises(ValidationError):
        ModelDecision(kind="clarification", question="  \n ")


def test_duplicate_capabilities_are_rejected():
    with pytest.raises(ValidationError):
        AIRequest(request_id="r1", message="price?", available_tools=["read_product"] * 2)


def test_duplicate_authorization_tools_are_rejected(grant):
    with pytest.raises(ValidationError):
        type(grant).model_validate(grant.model_dump() | {"allowed_tools": ["add_to_cart"] * 2})


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "answered"},
        {"status": "tool_proposed"},
        {"status": "blocked"},
        {"status": "error"},
        {"code": "WRONG_PRODUCT"},
        {"retryable": True},
        {"message": "  "},
        {"expected_document_id": "doc_1"},
        {"tool": {"name": "read_cart"}},
    ],
)
def test_inconsistent_responses_are_rejected(changes):
    from shopping_ai import AIResponse

    payload = dict(
        request_id="r1",
        status="needs_input",
        message="Which product?",
        provider="mock",
        prompt_version="v1",
    )
    with pytest.raises(ValidationError):
        AIResponse.model_validate(payload | changes)


def test_mutation_response_requires_document_binding():
    from shopping_ai import AIResponse

    with pytest.raises(ValidationError, match="document binding"):
        AIResponse(
            request_id="r1",
            status="tool_proposed",
            message="Proposal only",
            provider="mock",
            prompt_version="v1",
            tool=ToolProposal(name="select_variant", product_id="p42", variant_id="black"),
        )
