import decimal
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.alert import AlertNotification, PriceAlert
from app.models.product import Listing, ProductSnapshot
from app.models.session import Message, Session as ChatSession
from app.models.user import AuthToken, User

settings = get_settings()


def test_internal_job_secret_auth(client: TestClient):
    # Missing secret header -> 401
    r1 = client.post("/internal/jobs/run")
    assert r1.status_code == 401
    assert r1.json()["error"]["code"] == "unauthorized"

    # Wrong secret header -> 401
    r2 = client.post("/internal/jobs/run", headers={"X-Job-Secret": "wrong-secret"})
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "unauthorized"


def test_internal_job_cleanup_and_idempotency(
    client: TestClient,
    db_session: Session,
):
    now = datetime.now(timezone.utc)

    # 1. Setup stale items to be purged
    user = User(is_anonymous=True)
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        retailer="amazon",
        retailer_product_id=f"CLEANUP_{uuid.uuid4().hex[:6]}",
        title="Cleanup Item",
    )
    db_session.add(listing)
    db_session.flush()

    # Stale token (expired yesterday)
    stale_token = AuthToken(
        user_id=user.id,
        token_hash="expired_hash_123",
        expires_at=now - timedelta(days=2),
    )
    # Stale snapshot (35 days old > 30)
    stale_snapshot = ProductSnapshot(
        user_id=user.id,
        listing_id=listing.id,
        captured_price=decimal.Decimal("50.00"),
        availability="in_stock",
        created_at=now - timedelta(days=35),
    )
    # Stale session and message (35 days old > 30)
    session = ChatSession(user_id=user.id)
    db_session.add(session)
    db_session.flush()

    stale_msg = Message(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content="Old message",
        created_at=now - timedelta(days=35),
    )
    # Stale alert and notification (95 days old > 90)
    alert = PriceAlert(
        user_id=user.id,
        listing_id=listing.id,
        target_price=decimal.Decimal("45.00"),
        status="triggered",
    )
    db_session.add(alert)
    db_session.flush()

    stale_notif = AlertNotification(
        alert_id=alert.id,
        user_id=user.id,
        arm_count=1,
        channel="extension",
        status="delivered",
        payload={},
        created_at=now - timedelta(days=95),
    )

    db_session.add_all([stale_token, stale_snapshot, stale_msg, stale_notif])
    db_session.flush()

    headers = {"X-Job-Secret": settings.JOB_SECRET}

    # 2. Run maintenance job first time -> purges the items
    resp1 = client.post("/internal/jobs/run", headers=headers)
    assert resp1.status_code == 200
    res1 = resp1.json()
    assert res1["expired_tokens"] >= 1
    assert res1["deleted_snapshots"] >= 1
    assert res1["deleted_messages"] >= 1
    assert res1["deleted_notifications"] >= 1

    # 3. Run maintenance job immediately again -> idempotent, succeeds with 0 purged
    resp2 = client.post("/internal/jobs/run", headers=headers)
    assert resp2.status_code == 200
    res2 = resp2.json()
    assert res2["expired_tokens"] == 0
    assert res2["deleted_snapshots"] == 0
    assert res2["deleted_messages"] == 0
    assert res2["deleted_notifications"] == 0
