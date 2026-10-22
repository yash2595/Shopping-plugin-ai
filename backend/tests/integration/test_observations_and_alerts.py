import decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alert import AlertNotification, PriceAlert
from app.models.product import Listing, PriceObservation


def create_test_listing(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    price: str = "100.00",
) -> Listing:
    resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "bestbuy",
            "retailer_product_id": f"SKU_{uuid.uuid4().hex[:8]}",
            "url": "https://bestbuy.com/tv",
            "title": "4K Smart TV",
            "price": price,
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    listing_id = resp.json()["listing_id"]
    return db_session.get(Listing, uuid.UUID(listing_id))


def test_observation_below_target_triggers_alert_and_second_lower_does_not(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    listing = create_test_listing(client, auth_headers, db_session, price="520.00")

    # 1. Create alert at target_price = 500.00
    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": str(listing.id), "target_price": "500.00"},
        headers=auth_headers,
    )
    assert alert_resp.status_code == 201
    alert_id = alert_resp.json()["id"]

    # 2. First observation below target at 480.00
    obs1 = client.post(
        f"/v1/listings/{listing.id}/observations",
        json={"price": "480.00", "in_stock": True},
        headers=auth_headers,
    )
    assert obs1.status_code == 200
    obs1_data = obs1.json()
    assert obs1_data["recorded"] is True
    assert obs1_data["status"] == "accepted"
    assert len(obs1_data["triggered_alerts"]) == 1
    assert obs1_data["triggered_alerts"][0]["alert_id"] == alert_id

    # Verify exactly one pending notification created in DB
    notifications = list(
        db_session.scalars(
            select(AlertNotification).where(AlertNotification.alert_id == uuid.UUID(alert_id))
        ).all()
    )
    assert len(notifications) == 1
    assert notifications[0].status == "pending"
    assert notifications[0].arm_count == 1

    # Verify alert status is 'triggered'
    alert = db_session.get(PriceAlert, uuid.UUID(alert_id))
    assert alert.status == "triggered"
    assert alert.triggered_price == decimal.Decimal("480.00")

    # 3. Second lower observation at 450.00 triggers NOTHING
    obs2 = client.post(
        f"/v1/listings/{listing.id}/observations",
        json={"price": "450.00", "in_stock": True},
        headers=auth_headers,
    )
    assert obs2.status_code == 200
    obs2_data = obs2.json()
    assert obs2_data["recorded"] is True
    assert obs2_data["status"] == "accepted"
    assert len(obs2_data["triggered_alerts"]) == 0

    # Notification count remains exactly 1
    notifications_after = list(
        db_session.scalars(
            select(AlertNotification).where(AlertNotification.alert_id == uuid.UUID(alert_id))
        ).all()
    )
    assert len(notifications_after) == 1


def test_observation_above_target_does_not_trigger(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    listing = create_test_listing(client, auth_headers, db_session)

    # Alert target: 300.00
    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": str(listing.id), "target_price": "300.00"},
        headers=auth_headers,
    )
    alert_id = alert_resp.json()["id"]

    # Observation at 350.00 (> target)
    obs = client.post(
        f"/v1/listings/{listing.id}/observations",
        json={"price": "350.00", "in_stock": True},
        headers=auth_headers,
    )
    assert obs.status_code == 200
    assert len(obs.json()["triggered_alerts"]) == 0

    # Alert remains active
    alert = db_session.get(PriceAlert, uuid.UUID(alert_id))
    assert alert.status == "active"
    assert alert.triggered_at is None


def test_pending_drop_observation_does_not_trigger(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # Establish initial snapshot at 1000.00
    resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "bestbuy",
            "retailer_product_id": f"SKU_{uuid.uuid4().hex[:8]}",
            "url": "https://bestbuy.com/tv",
            "title": "4K Smart TV",
            "price": "1000.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    listing_id = resp.json()["listing_id"]
    listing = db_session.get(Listing, uuid.UUID(listing_id))

    # 1. Establish initial accepted observation at 1000.00
    obs1 = client.post(
        f"/v1/listings/{listing.id}/observations",
        json={"price": "1000.00", "in_stock": True},
        headers=auth_headers,
    )
    assert obs1.status_code == 200

    # 2. Set alert at 600.00
    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": str(listing.id), "target_price": "600.00"},
        headers=auth_headers,
    )
    alert_id = alert_resp.json()["id"]

    # 3. Observation drops by 60% to 400.00 (>50% jump threshold)
    # Status becomes 'pending', which must NEVER trigger alerts!
    obs2 = client.post(
        f"/v1/listings/{listing.id}/observations",
        json={"price": "400.00", "in_stock": True},
        headers=auth_headers,
    )
    assert obs2.status_code == 200
    assert obs2.json()["status"] == "pending"
    assert len(obs2.json()["triggered_alerts"]) == 0

    # Alert remains active, no notifications created
    alert = db_session.get(PriceAlert, uuid.UUID(alert_id))
    assert alert.status == "active"


def test_notifications_list_and_ack(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    listing = create_test_listing(client, auth_headers, db_session)
    client.post(
        "/v1/alerts",
        json={"listing_id": str(listing.id), "target_price": "100.00"},
        headers=auth_headers,
    )
    # Trigger the alert
    client.post(
        f"/v1/listings/{listing.id}/observations",
        json={"price": "90.00", "in_stock": True},
        headers=auth_headers,
    )

    # 1. List notifications (pending)
    list_resp = client.get("/v1/notifications?status=pending", headers=auth_headers)
    assert list_resp.status_code == 200
    notifications = list_resp.json()
    assert len(notifications) >= 1
    notif_id = notifications[0]["id"]
    assert notifications[0]["status"] == "pending"

    # 2. Acknowledge notification
    ack_resp = client.post(f"/v1/notifications/{notif_id}/ack", headers=auth_headers)
    assert ack_resp.status_code == 200
    ack_data = ack_resp.json()
    assert ack_data["status"] == "delivered"
    assert ack_data["delivered_at"] is not None

    # 3. Querying pending notifications now excludes it
    remaining_pending = client.get("/v1/notifications?status=pending", headers=auth_headers).json()
    assert not any(n["id"] == notif_id for n in remaining_pending)
