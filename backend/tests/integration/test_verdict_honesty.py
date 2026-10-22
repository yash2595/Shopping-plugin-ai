import decimal
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.product import Listing, PriceObservation


def test_three_points_within_one_hour_returns_history_too_short(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    now = datetime.now(timezone.utc)
    listing = Listing(
        retailer="bestbuy",
        retailer_product_id=f"HONEST_1H_{uuid.uuid4().hex[:8]}",
        title="Gaming Monitor 27-inch",
        url="https://bestbuy.com/monitor",
    )
    db_session.add(listing)
    db_session.flush()

    # 3 accepted observations within 50 minutes (span < 7 days)
    obs1 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("350.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(minutes=50),
        recorded_at=now - timedelta(minutes=50),
    )
    obs2 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("340.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(minutes=25),
        recorded_at=now - timedelta(minutes=25),
    )
    obs3 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("330.00"),
        in_stock=True,
        status="accepted",
        captured_at=now,
        recorded_at=now,
    )
    db_session.add_all([obs1, obs2, obs3])
    db_session.commit()

    resp = client.get(f"/v1/listings/{listing.id}/history", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["verdict"] == "insufficient_data"
    assert data["reason"] == "history_too_short"
    assert data["tracking_since"] is not None


def test_three_points_over_eight_days_returns_verdict(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    now = datetime.now(timezone.utc)
    listing = Listing(
        retailer="amazon",
        retailer_product_id=f"HONEST_8D_{uuid.uuid4().hex[:8]}",
        title="Ergonomic Office Chair",
        url="https://amazon.com/chair",
    )
    db_session.add(listing)
    db_session.flush()

    # 3 accepted observations over 8 days (span >= 7 days)
    obs1 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("250.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(days=8),
        recorded_at=now - timedelta(days=8),
    )
    obs2 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("230.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(days=4),
        recorded_at=now - timedelta(days=4),
    )
    obs3 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("200.00"),
        in_stock=True,
        status="accepted",
        captured_at=now,
        recorded_at=now,
    )
    db_session.add_all([obs1, obs2, obs3])
    db_session.commit()

    resp = client.get(f"/v1/listings/{listing.id}/history", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["verdict"] == "good_deal"  # 200.00 <= min
    assert data["reason"] is None
    assert data["tracking_since"] is not None
    assert decimal.Decimal(data["min"]) == decimal.Decimal("200.00")
    assert decimal.Decimal(data["max"]) == decimal.Decimal("250.00")


def test_two_points_over_thirty_days_returns_not_enough_observations(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    now = datetime.now(timezone.utc)
    listing = Listing(
        retailer="walmart",
        retailer_product_id=f"HONEST_30D_{uuid.uuid4().hex[:8]}",
        title="Cast Iron Skillet",
        url="https://walmart.com/skillet",
    )
    db_session.add(listing)
    db_session.flush()

    # 2 accepted observations over 30 days (count < 3)
    obs1 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("45.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(days=30),
        recorded_at=now - timedelta(days=30),
    )
    obs2 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("40.00"),
        in_stock=True,
        status="accepted",
        captured_at=now,
        recorded_at=now,
    )
    db_session.add_all([obs1, obs2])
    db_session.commit()

    resp = client.get(f"/v1/listings/{listing.id}/history?days=60", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["verdict"] == "insufficient_data"
    assert data["reason"] == "not_enough_observations"
    assert data["tracking_since"] is not None


def test_chat_grounding_and_mock_provider_when_history_too_short(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    now = datetime.now(timezone.utc)
    listing = Listing(
        retailer="target",
        retailer_product_id=f"CHAT_HONEST_{uuid.uuid4().hex[:8]}",
        title="Wireless Earbuds",
        url="https://target.com/earbuds",
    )
    db_session.add(listing)
    db_session.flush()

    # 2 observations 2 days ago
    obs1 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("120.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(days=2),
        recorded_at=now - timedelta(days=2),
    )
    obs2 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("115.00"),
        in_stock=True,
        status="accepted",
        captured_at=now - timedelta(days=1),
        recorded_at=now - timedelta(days=1),
    )
    db_session.add_all([obs1, obs2])
    db_session.commit()

    # Create snapshot (3rd observation today -> total count=3, span=2.0 days < 7 days)
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "target",
            "retailer_product_id": listing.retailer_product_id,
            "title": "Wireless Earbuds",
            "price": "110.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap_resp.status_code == 201
    snap_data = snap_resp.json()
    assert snap_data["price_summary"]["verdict"] == "insufficient_data"
    assert snap_data["price_summary"]["reason"] == "history_too_short"
    snapshot_id = snap_data["snapshot_id"]

    # Start chat session
    sess_resp = client.post("/v1/sessions", json={"title": "Deal Inquiry"}, headers=auth_headers)
    assert sess_resp.status_code == 201
    session_id = sess_resp.json()["id"]

    # Ask if it is a good deal
    msg_resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "request_id": f"req_verdict_test_{uuid.uuid4().hex[:8]}",
            "snapshot_id": snapshot_id,
            "text": "Is this a good deal?",
        },
        headers=auth_headers,
    )
    assert msg_resp.status_code == 200
    msg_data = msg_resp.json()
    assert msg_data["role"] == "assistant"
    content = msg_data["content"]
    # MockProvider states how long tracking has been active using supplied numbers
    assert "tracking has been active for 2.0 days" in content.lower()
    assert "110.00" in content
