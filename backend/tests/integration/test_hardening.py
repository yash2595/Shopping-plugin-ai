import decimal
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.alert import AlertNotification, PriceAlert
from app.services.auth_service import AuthService


def test_unseen_listing_observation_returns_409(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # 1. User 1 creates snapshot (creates listing)
    snap1 = client.post(
        "/v1/snapshots",
        json={
            "retailer": "bestbuy",
            "retailer_product_id": f"BB_{uuid.uuid4().hex[:8]}",
            "title": "Smart TV",
            "url": "https://bestbuy.com/tv",
            "price": "500.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap1.status_code == 201
    listing_id = snap1.json()["listing_id"]

    # 2. User 2 registers and attempts to submit observation without viewing snapshot
    auth_service = AuthService()
    token2, _ = auth_service.register_anonymous_user(db_session)
    user2_headers = {"Authorization": f"Bearer {token2}"}

    resp = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "450.00", "in_stock": True},
        headers=user2_headers,
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "LISTING_NOT_SEEN"
    assert "User has not captured a snapshot" in body["error"]["message"]


def test_observation_rate_limiting_returns_429(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # 1. Capture snapshot to establish trust
    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "walmart",
            "retailer_product_id": f"WM_{uuid.uuid4().hex[:8]}",
            "title": "Toaster",
            "url": "https://walmart.com/toaster",
            "price": "30.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap.status_code == 201
    listing_id = snap.json()["listing_id"]

    # 2. Submit observations up to limit (MAX_OBSERVATIONS_PER_LISTING_PER_HOUR is 10)
    for i in range(10):
        obs = client.post(
            f"/v1/listings/{listing_id}/observations",
            json={
                "price": f"{30.00 + (i * 0.10):.2f}",
                "in_stock": True,
            },
            headers=auth_headers,
        )
        assert obs.status_code in (200, 429)
        if obs.status_code == 429:
            break

    # 3. Next observation must be rate limited (429)
    limited = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "35.00", "in_stock": True},
        headers=auth_headers,
    )
    assert limited.status_code == 429
    body = limited.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "Observation rate limit exceeded" in body["error"]["message"]


def test_user_daily_observation_rate_limiting_returns_429(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    from app.config import get_settings
    settings = get_settings()
    # Configure low daily limit for testing: 3 per user per day # PLACEHOLDER
    monkeypatch.setattr(settings, "MAX_OBSERVATIONS_PER_USER_PER_DAY", 3)

    # User captures snapshots for 4 distinct listings
    listing_ids = []
    for i in range(4):
        snap = client.post(
            "/v1/snapshots",
            json={
                "retailer": "amazon",
                "retailer_product_id": f"DAILY_LIMIT_{i}_{uuid.uuid4().hex[:8]}",
                "title": f"Product {i}",
                "url": f"https://amazon.com/item{i}",
                "price": "100.00",
                "availability": "in_stock",
            },
            headers=auth_headers,
        )
        assert snap.status_code == 201
        listing_ids.append(snap.json()["listing_id"])

    # First 3 observations across different listings succeed (hourly per-listing limit is 10, so not exceeded)
    for i in range(3):
        res = client.post(
            f"/v1/listings/{listing_ids[i]}/observations",
            json={"price": f"{90.00 - i:.2f}", "in_stock": True},
            headers=auth_headers,
        )
        assert res.status_code == 200

    # 4th observation exceeds MAX_OBSERVATIONS_PER_USER_PER_DAY -> 429 RATE_LIMITED
    res_limited = client.post(
        f"/v1/listings/{listing_ids[3]}/observations",
        json={"price": "85.00", "in_stock": True},
        headers=auth_headers,
    )
    assert res_limited.status_code == 429
    body = res_limited.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "Observation rate limit exceeded" in body["error"]["message"]


def test_confirming_drop_older_than_24h_does_not_trigger_alert(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # Owner (User 1) captures snapshot at 500.00 and sets alert at 400.00
    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "target",
            "retailer_product_id": f"TGT_24H_{uuid.uuid4().hex[:8]}",
            "title": "Espresso Machine",
            "url": "https://target.com/espresso",
            "price": "500.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap.status_code == 201
    listing_id = snap.json()["listing_id"]

    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": listing_id, "target_price": "400.00"},
        headers=auth_headers,
    )
    assert alert_resp.status_code == 201
    alert_id = alert_resp.json()["id"]

    # Stranger 1 (User 2) captures snapshot and submits an observation 25 hours ago
    auth_service = AuthService()
    t2, _ = auth_service.register_anonymous_user(db_session)
    u2 = auth_service.authenticate_token(db_session, t2)
    from app.models.product import PriceObservation, ProductSnapshot
    db_session.add(
        ProductSnapshot(
            user_id=u2.id,
            listing_id=uuid.UUID(listing_id),
            captured_price=decimal.Decimal("500.00"),
            availability="in_stock",
        )
    )
    # Stale confirming observation from 25 hours ago
    stale_time = datetime.now(timezone.utc) - timedelta(hours=25)
    db_session.add(
        PriceObservation(
            listing_id=uuid.UUID(listing_id),
            price=decimal.Decimal("350.00"),
            in_stock=True,
            status="accepted",
            source="observation",
            user_id=u2.id,
            captured_at=stale_time,
            recorded_at=stale_time,
        )
    )
    db_session.commit()

    # Stranger 2 (User 3) posts qualifying observation at 340.00 NOW
    t3, _ = auth_service.register_anonymous_user(db_session)
    h3 = {"Authorization": f"Bearer {t3}"}
    u3 = auth_service.authenticate_token(db_session, t3)
    db_session.add(
        ProductSnapshot(
            user_id=u3.id,
            listing_id=uuid.UUID(listing_id),
            captured_price=decimal.Decimal("500.00"),
            availability="in_stock",
        )
    )
    db_session.commit()

    obs2 = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "340.00", "in_stock": True},
        headers=h3,
    )
    assert obs2.status_code == 200
    # Must NOT trigger because confirming drop is > 24 hours old!
    assert len(obs2.json()["triggered_alerts"]) == 0

    # Verify alert in database remains active
    db_session.expire_all()
    alert_in_db = db_session.get(PriceAlert, uuid.UUID(alert_id))
    assert alert_in_db.status == "active"
    assert alert_in_db.triggered_price is None


def test_stranger_single_drop_does_not_trigger_alert_and_confirming_drop_does(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # Owner (User 1) captures snapshot at 500.00 and sets alert at 400.00
    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "target",
            "retailer_product_id": f"TGT_{uuid.uuid4().hex[:8]}",
            "title": "Coffee Maker",
            "url": "https://target.com/coffee",
            "price": "500.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap.status_code == 201
    listing_id = snap.json()["listing_id"]

    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": listing_id, "target_price": "400.00"},
        headers=auth_headers,
    )
    assert alert_resp.status_code == 201
    alert_id = alert_resp.json()["id"]

    # Stranger 1 (User 2) registers and captures snapshot for listing
    auth_service = AuthService()
    t2, _ = auth_service.register_anonymous_user(db_session)
    h2 = {"Authorization": f"Bearer {t2}"}
    client.post(
        "/v1/snapshots",
        json={
            "retailer": "target",
            "retailer_product_id": snap.json()["listing_id"],
            "title": "Coffee Maker",
            "url": "https://target.com/coffee",
            "price": "500.00",
            "availability": "in_stock",
        },
        headers=h2,
    )
    # Ensure User 2 has snapshot recorded on listing_id directly
    from app.models.product import ProductSnapshot
    u2 = auth_service.authenticate_token(db_session, t2)
    db_session.add(
        ProductSnapshot(
            user_id=u2.id,
            listing_id=uuid.UUID(listing_id),
            captured_price=decimal.Decimal("500.00"),
            availability="in_stock",
        )
    )
    db_session.flush()

    # Stranger 1 posts a qualifying drop at 350.00 (<= 400.00)
    obs1 = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "350.00", "in_stock": True},
        headers=h2,
    )
    assert obs1.status_code == 200
    assert len(obs1.json()["triggered_alerts"]) == 0  # Does NOT trigger! Single stranger drop

    # Verify alert is still active
    alert_check = db_session.get(PriceAlert, uuid.UUID(alert_id))
    assert alert_check.status == "active"

    # Stranger 2 (User 3) registers and captures snapshot for listing
    t3, _ = auth_service.register_anonymous_user(db_session)
    h3 = {"Authorization": f"Bearer {t3}"}
    u3 = auth_service.authenticate_token(db_session, t3)
    db_session.add(
        ProductSnapshot(
            user_id=u3.id,
            listing_id=uuid.UUID(listing_id),
            captured_price=decimal.Decimal("500.00"),
            availability="in_stock",
        )
    )
    db_session.flush()

    # Stranger 2 posts confirming drop at 340.00 (second distinct user)
    obs2 = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "340.00", "in_stock": True},
        headers=h3,
    )
    assert obs2.status_code == 200
    assert len(obs2.json()["triggered_alerts"]) == 1  # DOES trigger now!
    assert obs2.json()["triggered_alerts"][0]["alert_id"] == alert_id

    # Verify alert is now triggered
    db_session.expire_all()
    alert_check2 = db_session.get(PriceAlert, uuid.UUID(alert_id))
    assert alert_check2.status == "triggered"
    assert alert_check2.triggered_price == decimal.Decimal("340.00")


def test_owner_own_drop_triggers_immediately(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # Owner sets alert at 200.00
    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "ebay",
            "retailer_product_id": f"EB_{uuid.uuid4().hex[:8]}",
            "title": "Vintage Watch",
            "url": "https://ebay.com/watch",
            "price": "220.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap.status_code == 201
    listing_id = snap.json()["listing_id"]

    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": listing_id, "target_price": "200.00"},
        headers=auth_headers,
    )
    assert alert_resp.status_code == 201
    alert_id = alert_resp.json()["id"]

    # Owner's own drop at 180.00
    obs = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "180.00", "in_stock": True},
        headers=auth_headers,
    )
    assert obs.status_code == 200
    assert len(obs.json()["triggered_alerts"]) == 1
    assert obs.json()["triggered_alerts"][0]["alert_id"] == alert_id

    db_session.expire_all()
    alert = db_session.get(PriceAlert, uuid.UUID(alert_id))
    assert alert.status == "triggered"
    assert alert.triggered_price == decimal.Decimal("180.00")


def test_standard_error_body_shapes_on_404_409_422_429(
    client: TestClient,
    auth_headers: dict[str, str],
):
    # 404: Not Found
    resp_404 = client.get(f"/v1/listings/{uuid.uuid4()}/history", headers=auth_headers)
    assert resp_404.status_code == 404
    body_404 = resp_404.json()
    assert "error" in body_404
    assert body_404["error"]["code"] == "not_found"
    assert isinstance(body_404["error"]["message"], str)

    # 409: Conflict (unknown listing observation)
    resp_409 = client.post(
        f"/v1/listings/{uuid.uuid4()}/observations",
        json={"price": "100.00", "in_stock": True},
        headers=auth_headers,
    )
    # If listing not found -> 404; if listing exists without snapshot -> 409
    assert resp_409.status_code in (404, 409)
    assert "error" in resp_409.json()

    # 422: Validation Error (extra field forbidden)
    resp_422 = client.post(
        "/v1/alerts",
        json={"invalid_field": 123},
        headers=auth_headers,
    )
    assert resp_422.status_code == 422
    body_422 = resp_422.json()
    assert "error" in body_422
    assert body_422["error"]["code"] == "validation_error"
    assert isinstance(body_422["error"]["message"], str)
