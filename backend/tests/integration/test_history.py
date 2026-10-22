import decimal
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.product import Listing, PriceObservation


def test_history_returns_accepted_only_and_insufficient_data_with_less_than_3(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    listing = Listing(
        retailer="walmart",
        retailer_product_id=f"HIST_{uuid.uuid4().hex[:8]}",
        title="Standing Desk Converter",
        url="https://walmart.com/desk",
    )
    db_session.add(listing)
    db_session.flush()

    now = datetime.now(timezone.utc)

    # 1. Zero observations -> count=0, verdict='insufficient_data'
    h0 = client.get(f"/v1/listings/{listing.id}/history", headers=auth_headers).json()
    assert h0["count"] == 0
    assert h0["verdict"] == "insufficient_data"
    assert h0["min"] is None
    assert h0["first_seen"] is None

    # 2. Add 1 accepted observation and 1 pending observation
    obs1 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("200.00"),
        in_stock=True,
        status="accepted",
        source="extension_snapshot",
        captured_at=now - timedelta(days=8),
        recorded_at=now - timedelta(days=8),
    )
    obs_pending = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("50.00"),
        in_stock=True,
        status="pending",  # Pending must NOT appear in history
        source="extension_snapshot",
        captured_at=now - timedelta(days=7),
        recorded_at=now - timedelta(days=7),
    )
    db_session.add_all([obs1, obs_pending])
    db_session.flush()

    h1 = client.get(f"/v1/listings/{listing.id}/history", headers=auth_headers).json()
    assert h1["count"] == 1
    assert h1["verdict"] == "insufficient_data"
    assert h1["reason"] == "not_enough_observations"
    assert h1["min"] is None
    assert h1["observations"][0]["price"] == "200.00"

    # 3. Add 2nd accepted observation -> still < 3 -> insufficient_data
    obs2 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("190.00"),
        in_stock=True,
        status="accepted",
        source="extension_snapshot",
        captured_at=now - timedelta(days=4),
        recorded_at=now - timedelta(days=4),
    )
    db_session.add(obs2)
    db_session.flush()

    h2 = client.get(f"/v1/listings/{listing.id}/history", headers=auth_headers).json()
    assert h2["count"] == 2
    assert h2["verdict"] == "insufficient_data"
    assert h2["reason"] == "not_enough_observations"
    assert h2["min"] is None

    # 4. Add 3rd accepted observation across 8 days -> count=3, span=8 days -> computes real verdict and stats
    obs3 = PriceObservation(
        listing_id=listing.id,
        price=decimal.Decimal("180.00"),
        in_stock=True,
        status="accepted",
        source="extension_snapshot",
        captured_at=now,
        recorded_at=now,
    )
    db_session.add(obs3)
    db_session.flush()

    h3 = client.get(f"/v1/listings/{listing.id}/history", headers=auth_headers).json()
    assert h3["count"] == 3
    assert decimal.Decimal(h3["min"]) == decimal.Decimal("180.00")
    assert decimal.Decimal(h3["max"]) == decimal.Decimal("200.00")
    assert decimal.Decimal(h3["average"]) == decimal.Decimal("190.00")
    assert h3["verdict"] == "good_deal"  # latest is 180 <= min (180)
    assert h3["reason"] is None
    assert h3["first_seen"] is not None
    assert h3["tracking_since"] is not None


def test_history_not_found(client: TestClient, auth_headers: dict[str, str]):
    random_id = uuid.uuid4()
    resp = client.get(f"/v1/listings/{random_id}/history", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
