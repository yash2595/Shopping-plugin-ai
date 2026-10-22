import decimal
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Listing, PriceObservation


def test_create_snapshot_returns_201_and_insufficient_data(
    client: TestClient,
    auth_headers: dict[str, str],
):
    payload = {
        "retailer": "amazon",
        "retailer_product_id": "B08N5WRWNW",
        "title": "Sony WH-1000XM4 Wireless Headphones",
        "url": "https://www.amazon.com/dp/B08N5WRWNW",
        "price": "348.00",
        "availability": "in_stock",
        "document_id": "doc_123",
        "missing_fields": [],
    }

    response = client.post("/v1/snapshots", json=payload, headers=auth_headers)
    assert response.status_code == 201

    data = response.json()
    assert "snapshot_id" in data
    assert "listing_id" in data
    assert data["price_recorded"] is True
    assert data["price_summary"]["verdict"] == "insufficient_data"
    assert data["price_summary"]["lowest_price"] is None
    assert data["price_summary"]["highest_price"] is None


def test_identical_observation_within_6h_returns_recorded_false(
    client: TestClient,
    auth_headers: dict[str, str],
):
    payload = {
        "retailer": "bestbuy",
        "retailer_product_id": "6429440",
        "title": "Apple iPad Air 64GB",
        "url": "https://www.bestbuy.com/site/6429440.p",
        "price": "499.99",
        "availability": "in_stock",
        "document_id": "doc_ipad",
        "missing_fields": [],
    }

    # First observation recorded -> price_recorded = True
    resp1 = client.post("/v1/snapshots", json=payload, headers=auth_headers)
    assert resp1.status_code == 201
    assert resp1.json()["price_recorded"] is True

    # Immediate second observation with identical price -> price_recorded = False (skipped)
    resp2 = client.post("/v1/snapshots", json=payload, headers=auth_headers)
    assert resp2.status_code == 201
    assert resp2.json()["price_recorded"] is False


def test_price_jump_over_50_percent_stored_as_pending(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # Base observation: 100.00
    base_payload = {
        "retailer": "walmart",
        "retailer_product_id": "WM_789123",
        "title": "Mechanical Gaming Keyboard",
        "url": "https://www.walmart.com/ip/789123",
        "price": "100.00",
        "availability": "in_stock",
        "document_id": "doc_kb_1",
        "missing_fields": [],
    }
    resp1 = client.post("/v1/snapshots", json=base_payload, headers=auth_headers)
    assert resp1.status_code == 201
    listing_id = resp1.json()["listing_id"]

    # Verify first observation is accepted
    obs1 = db_session.scalars(
        select(PriceObservation)
        .where(PriceObservation.listing_id == listing_id, PriceObservation.price == decimal.Decimal("100.00"))
    ).first()
    assert obs1 is not None
    assert obs1.status == "accepted"

    # Price jumps by 60% to 160.00 (>50% jump threshold)
    jump_payload = {
        "retailer": "walmart",
        "retailer_product_id": "WM_789123",
        "title": "Mechanical Gaming Keyboard",
        "url": "https://www.walmart.com/ip/789123",
        "price": "160.00",
        "availability": "in_stock",
        "document_id": "doc_kb_2",
        "missing_fields": [],
    }
    resp2 = client.post("/v1/snapshots", json=jump_payload, headers=auth_headers)
    assert resp2.status_code == 201
    assert resp2.json()["price_recorded"] is True

    # Verify the jumped observation is stored as status='pending'
    obs2 = db_session.scalars(
        select(PriceObservation)
        .where(PriceObservation.listing_id == listing_id, PriceObservation.price == decimal.Decimal("160.00"))
    ).first()
    assert obs2 is not None
    assert obs2.status == "pending"


def test_normalizes_retailer_product_id(
    client: TestClient,
    auth_headers: dict[str, str],
):
    # Amazon retailer product id normalized to uppercase and trimmed
    payload1 = {
        "retailer": "  Amazon  ",
        "retailer_product_id": "  b08n5wrwnw  ",
        "title": "Normalized Title 1",
        "price": "199.99",
        "availability": "in_stock",
    }
    resp1 = client.post("/v1/snapshots", json=payload1, headers=auth_headers)
    assert resp1.status_code == 201
    listing_id_1 = resp1.json()["listing_id"]

    payload2 = {
        "retailer": "amazon",
        "retailer_product_id": "B08N5WRWNW",
        "title": "Normalized Title 2",
        "price": "189.99",
        "availability": "in_stock",
    }
    resp2 = client.post("/v1/snapshots", json=payload2, headers=auth_headers)
    assert resp2.status_code == 201
    listing_id_2 = resp2.json()["listing_id"]

    # Both refer to the identical normalized listing
    assert listing_id_1 == listing_id_2


def test_snapshot_price_summary_uses_real_history_when_three_or_more_points(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    now = datetime.now(timezone.utc)
    listing = Listing(
        retailer="target",
        retailer_product_id="TARGET_999",
        title="Espresso Machine",
    )
    db_session.add(listing)
    db_session.flush()

    # Pre-populate 2 historical accepted observations across 10 days
    obs1 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("300.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(days=10),
        recorded_at=now - timedelta(days=10),
    )
    obs2 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("280.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(days=5),
        recorded_at=now - timedelta(days=5),
    )
    db_session.add_all([obs1, obs2])
    db_session.flush()

    # Submit a snapshot with 3rd observation at 250.00
    snapshot_payload = {
        "retailer": "target",
        "retailer_product_id": "TARGET_999",
        "title": "Espresso Machine",
        "price": "250.00",
        "availability": "in_stock",
    }
    resp = client.post("/v1/snapshots", json=snapshot_payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()

    summary = data["price_summary"]
    assert summary["observation_count"] == 3
    assert summary["verdict"] == "good_deal"  # 250.00 <= lowest (250.00)
    assert summary["reason"] is None
    assert summary["tracking_since"] is not None
    assert decimal.Decimal(summary["lowest_price"]) == decimal.Decimal("250.00")
    assert decimal.Decimal(summary["highest_price"]) == decimal.Decimal("300.00")
    assert decimal.Decimal(summary["average_price"]) == decimal.Decimal("276.67")
