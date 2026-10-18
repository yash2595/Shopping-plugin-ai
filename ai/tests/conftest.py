from datetime import UTC, datetime, timedelta

import pytest

from shopping_ai.models import AIRequest, MutationAuthorization, ProductSnapshot, Variant

NOW = datetime(2026, 10, 18, 16, 30, tzinfo=UTC)


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def product():
    return ProductSnapshot(
        snapshot_id="snap_1",
        product_id="p42",
        document_id="doc_1",
        title="Demo Earbuds",
        observed_at=NOW,
        selected_variant_id="black",
        variants=[
            Variant(variant_id="black", label="Black"),
            Variant(variant_id="blue", label="Blue"),
        ],
        price="1499.00",
        currency="INR",
        in_stock=True,
        specs={"Battery": "Up to 30 hours (retailer claim)"},
    )


@pytest.fixture
def grant():
    return MutationAuthorization(
        request_id="r1",
        allowed_tools=["select_variant", "add_to_cart"],
        product_id="p42",
        document_id="doc_1",
        variant_id="black",
        quantity=1,
        mode="ensure_quantity",
        max_unit_price="1999.00",
        currency="INR",
        expires_at=NOW + timedelta(minutes=2),
    )


@pytest.fixture
def ai_request(product):
    return AIRequest(
        request_id="r1",
        message="Iska price aur colour?",
        product=product,
        available_tools=["read_product", "read_cart", "select_variant", "add_to_cart"],
    )
