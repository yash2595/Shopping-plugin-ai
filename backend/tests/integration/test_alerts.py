import decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.product import Listing
from app.services.auth_service import AuthService


def create_test_listing(db_session: Session) -> Listing:
    listing = Listing(
        retailer="amazon",
        retailer_product_id=f"TEST_PROD_{uuid.uuid4().hex[:8]}",
        title="Test Headphones",
        url="https://amazon.com/dp/test",
    )
    db_session.add(listing)
    db_session.flush()
    return listing


def test_create_alert_and_duplicate_409(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    listing = create_test_listing(db_session)

    payload = {
        "listing_id": str(listing.id),
        "target_price": "150.00",
    }

    # 1. First alert -> 201 Created
    resp1 = client.post("/v1/alerts", json=payload, headers=auth_headers)
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert data1["status"] == "active"
    assert data1["arm_count"] == 1
    assert data1["version"] == 1
    assert decimal.Decimal(data1["target_price"]) == decimal.Decimal("150.00")

    # 2. Second active alert for same listing -> 409 ALERT_ALREADY_EXISTS
    resp2 = client.post("/v1/alerts", json=payload, headers=auth_headers)
    assert resp2.status_code == 409
    err = resp2.json()["error"]
    assert err["code"] == "ALERT_ALREADY_EXISTS"


def test_another_user_alert_returns_404(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    listing = create_test_listing(db_session)

    # User 1 creates an alert
    resp = client.post(
        "/v1/alerts",
        json={"listing_id": str(listing.id), "target_price": "200.00"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    alert_id = resp.json()["id"]

    # User 2 attempts to patch User 1's alert
    auth_service = AuthService()
    user2_token, _ = auth_service.register_anonymous_user(db_session)
    user2_headers = {"Authorization": f"Bearer {user2_token}"}

    patch_resp = client.patch(
        f"/v1/alerts/{alert_id}",
        json={"version": 1, "target_price": "180.00"},
        headers=user2_headers,
    )
    assert patch_resp.status_code == 404
    assert patch_resp.json()["error"]["code"] == "not_found"

    # User 2 attempts to rearm User 1's alert
    rearm_resp = client.post(
        f"/v1/alerts/{alert_id}/rearm",
        headers=user2_headers,
    )
    assert rearm_resp.status_code == 404
    assert rearm_resp.json()["error"]["code"] == "not_found"


def test_patch_alert_optimistic_locking(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    listing = create_test_listing(db_session)
    create_resp = client.post(
        "/v1/alerts",
        json={"listing_id": str(listing.id), "target_price": "100.00"},
        headers=auth_headers,
    )
    alert_id = create_resp.json()["id"]
    current_version = create_resp.json()["version"]

    # Stale version -> 409
    stale_resp = client.patch(
        f"/v1/alerts/{alert_id}",
        json={"version": current_version + 5, "target_price": "95.00"},
        headers=auth_headers,
    )
    assert stale_resp.status_code == 409
    assert stale_resp.json()["error"]["code"] == "VERSION_CONFLICT"

    # Correct version -> 200 and version increments
    ok_resp = client.patch(
        f"/v1/alerts/{alert_id}",
        json={"version": current_version, "target_price": "95.00"},
        headers=auth_headers,
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["version"] == current_version + 1
    assert decimal.Decimal(ok_resp.json()["target_price"]) == decimal.Decimal("95.00")


def test_rearm_lifecycle(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # 1. Capture snapshot to establish listing and user trust per A1
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": f"TEST_PROD_{uuid.uuid4().hex[:8].upper()}",
            "url": "https://amazon.com/dp/test",
            "title": "Test Headphones",
            "price": "100.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap_resp.status_code == 201
    listing_id = snap_resp.json()["listing_id"]

    # 2. Create alert at 100.00
    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": listing_id, "target_price": "100.00"},
        headers=auth_headers,
    )
    alert_id = alert_resp.json()["id"]

    # 3. Rearm on active alert is rejected -> 400
    active_rearm = client.post(f"/v1/alerts/{alert_id}/rearm", headers=auth_headers)
    assert active_rearm.status_code == 400
    assert "Only triggered alerts can be rearmed" in active_rearm.json()["error"]["message"]

    # 4. Post observation to trigger alert
    obs_resp = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "90.00", "in_stock": True},
        headers=auth_headers,
    )
    assert obs_resp.status_code == 200
    assert len(obs_resp.json()["triggered_alerts"]) == 1

    # Verify alert is now triggered
    alerts_list = client.get("/v1/alerts?status=triggered", headers=auth_headers).json()
    assert any(a["id"] == alert_id for a in alerts_list)

    # 5. Rearm after trigger -> 200, status=active, arm_count=2
    rearmed_resp = client.post(f"/v1/alerts/{alert_id}/rearm", headers=auth_headers)
    assert rearmed_resp.status_code == 200
    rearmed_data = rearmed_resp.json()
    assert rearmed_data["status"] == "active"
    assert rearmed_data["arm_count"] == 2
    assert rearmed_data["triggered_at"] is None
