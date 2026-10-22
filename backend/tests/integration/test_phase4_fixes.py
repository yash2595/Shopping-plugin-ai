import decimal
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.alert import PriceAlert
from app.models.context import ShoppingContext
from app.models.product import PriceObservation
from app.models.review import ReviewSummary
from app.models.session import Session as ChatSession
from app.services.auth_service import AuthService

settings = get_settings()


def test_a1_recorded_at_server_time_and_future_timestamp_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    """
    Proves:
    1. captured_at > 5 minutes in the future returns 422 INVALID_TIMESTAMP.
    2. A fake old captured_at with a fresh server recorded_at is still evaluated by server time.
    """
    prod_id = f"p_{uuid.uuid4().hex[:6]}"
    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": prod_id,
            "title": "Server Time Item",
            "price": "1000.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    ).json()
    listing_id = snap["listing_id"]

    # 1. Reject future timestamp (> 5 min)
    future_time = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    r_future = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "950.00", "in_stock": True, "captured_at": future_time},
        headers=auth_headers,
    )
    assert r_future.status_code == 422
    assert r_future.json()["error"]["code"] == "INVALID_TIMESTAMP"

    # 2. Alert corroboration uses server recorded_at:
    # User 1 sets alert at 800.00
    alert_resp = client.post(
        "/v1/alerts",
        json={"listing_id": listing_id, "target_price": "800.00"},
        headers=auth_headers,
    ).json()

    # User 2 posts a fake old captured_at (e.g. 25 hours ago), but recorded_at is now!
    auth_service = AuthService()
    t2, _ = auth_service.register_anonymous_user(db_session)
    h2 = {"Authorization": f"Bearer {t2}"}
    u2 = auth_service.authenticate_token(db_session, t2)

    client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": prod_id,
            "title": "Server Time Item",
            "price": "1000.00",
            "availability": "in_stock",
        },
        headers=h2,
    )

    fake_old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    obs1 = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "750.00", "in_stock": True, "captured_at": fake_old_time},
        headers=h2,
    )
    assert obs1.status_code == 200

    # User 3 posts drop now: should corroborate because User 2's recorded_at is FRESH (<24h)!
    t3, _ = auth_service.register_anonymous_user(db_session)
    h3 = {"Authorization": f"Bearer {t3}"}
    client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": prod_id,
            "title": "Server Time Item",
            "price": "1000.00",
            "availability": "in_stock",
        },
        headers=h3,
    )

    obs2 = client.post(
        f"/v1/listings/{listing_id}/observations",
        json={"price": "740.00", "in_stock": True},
        headers=h3,
    )
    assert obs2.status_code == 200
    # Alert triggers because recorded_at (not client-sent captured_at) governed the 24h window
    assert len(obs2.json()["triggered_alerts"]) == 1


def test_d1_history_daily_resolution_lowest_price_per_day_and_raw(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    """
    Proves:
    1. GET /v1/listings/{id}/history?resolution=daily (default) returns lowest price point per day.
    2. resolution=raw returns every observation.
    3. Fewer than 3 points returns insufficient_data and min/max/avg None.
    """
    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": f"p_{uuid.uuid4().hex[:6]}",
            "title": "Daily History Product",
            "price": "5000.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    ).json()
    listing_id = snap["listing_id"]

    # 1. Initially only 1 observation -> insufficient_data
    h_init = client.get(f"/v1/listings/{listing_id}/history", headers=auth_headers).json()
    assert h_init["count"] == 1
    assert h_init["verdict"] == "insufficient_data"
    assert h_init["min"] is None
    assert h_init["average"] is None

    # Insert multiple observations across multiple days:
    # Day 1 (8 days ago): two prices 4800 and 4600 -> lowest is 4600
    # Day 2 (today): two prices 5200 and 5000 -> lowest is 5000
    now = datetime.now(timezone.utc)
    eight_days_ago = now - timedelta(days=8)

    db_session.add(
        PriceObservation(
            listing_id=uuid.UUID(listing_id),
            price=decimal.Decimal("4800.00"),
            in_stock=True,
            status="accepted",
            captured_at=eight_days_ago,
            recorded_at=eight_days_ago,
        )
    )
    db_session.add(
        PriceObservation(
            listing_id=uuid.UUID(listing_id),
            price=decimal.Decimal("4600.00"),
            in_stock=True,
            status="accepted",
            captured_at=eight_days_ago + timedelta(hours=2),
            recorded_at=eight_days_ago + timedelta(hours=2),
        )
    )
    db_session.add(
        PriceObservation(
            listing_id=uuid.UUID(listing_id),
            price=decimal.Decimal("5200.00"),
            in_stock=True,
            status="accepted",
            captured_at=now,
            recorded_at=now,
        )
    )
    db_session.commit()

    # 2. Query with resolution=daily (default)
    h_daily = client.get(f"/v1/listings/{listing_id}/history?resolution=daily", headers=auth_headers).json()
    assert h_daily["count"] == 4  # Total accepted observations count
    assert h_daily["min"] == "4600.00"
    assert h_daily["max"] == "5200.00"
    assert h_daily["verdict"] != "insufficient_data"
    # Observations list should have only 2 points (one for yesterday at 4600, one for today at 5000)
    # Plus snapshot at 5000 today: today lowest is 5000
    daily_obs = h_daily["observations"]
    assert len(daily_obs) == 2
    prices = [o["price"] for o in daily_obs]
    assert "4600.00" in prices
    assert "5000.00" in prices
    assert "4800.00" not in prices
    assert "5200.00" not in prices

    # 3. Query with resolution=raw
    h_raw = client.get(f"/v1/listings/{listing_id}/history?resolution=raw", headers=auth_headers).json()
    assert len(h_raw["observations"]) == 4


def test_d2_alerts_filter_by_listing_id(
    client: TestClient,
    auth_headers: dict[str, str],
):
    """Proves GET /v1/alerts?listing_id= filters alerts correctly by listing."""
    # Create two listings
    s1 = client.post(
        "/v1/snapshots",
        json={"retailer": "amazon", "retailer_product_id": f"p_{uuid.uuid4().hex[:6]}", "title": "Item 1", "price": "100.00", "availability": "in_stock"},
        headers=auth_headers,
    ).json()["listing_id"]

    s2 = client.post(
        "/v1/snapshots",
        json={"retailer": "amazon", "retailer_product_id": f"p_{uuid.uuid4().hex[:6]}", "title": "Item 2", "price": "200.00", "availability": "in_stock"},
        headers=auth_headers,
    ).json()["listing_id"]

    client.post("/v1/alerts", json={"listing_id": s1, "target_price": "80.00"}, headers=auth_headers)
    client.post("/v1/alerts", json={"listing_id": s2, "target_price": "150.00"}, headers=auth_headers)

    # Filter by s1
    res1 = client.get(f"/v1/alerts?listing_id={s1}", headers=auth_headers).json()
    assert len(res1) == 1
    assert res1[0]["listing_id"] == s1

    # Filter by s2
    res2 = client.get(f"/v1/alerts?listing_id={s2}", headers=auth_headers).json()
    assert len(res2) == 1
    assert res2[0]["listing_id"] == s2


def test_cleanup_job_deletes_inactive_sessions_and_old_review_summaries(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    """
    Proves POST /internal/jobs/run:
    - deletes sessions inactive for 90 days (with cascade on contexts and rejections)
    - deletes review summaries older than 30 days
    - is idempotent
    """
    auth_service = AuthService()
    user = auth_service.authenticate_token(db_session, auth_headers["Authorization"].split()[1])

    now = datetime.now(timezone.utc)
    stale_session_time = now - timedelta(days=95)
    fresh_session_time = now - timedelta(days=5)
    stale_review_time = now - timedelta(days=35)
    fresh_review_time = now - timedelta(days=5)

    # Create stale session with context
    stale_session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="Stale Session",
        created_at=stale_session_time,
        updated_at=stale_session_time,
    )
    db_session.add(stale_session)
    db_session.flush()

    stale_ctx = ShoppingContext(
        id=uuid.uuid4(),
        user_id=user.id,
        session_id=stale_session.id,
        version=1,
    )
    db_session.add(stale_ctx)

    # Create fresh session
    fresh_session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="Fresh Session",
        created_at=fresh_session_time,
        updated_at=fresh_session_time,
    )
    db_session.add(fresh_session)

    # Create listing for review summaries
    snap = client.post(
        "/v1/snapshots",
        json={"retailer": "amazon", "retailer_product_id": f"p_{uuid.uuid4().hex[:6]}", "title": "Cleanup Test", "price": "50.00", "availability": "in_stock"},
        headers=auth_headers,
    ).json()
    listing_id = uuid.UUID(snap["listing_id"])

    # Stale review summary
    stale_rev = ReviewSummary(
        id=uuid.uuid4(),
        listing_id=listing_id,
        reviews_hash="a" * 64,
        sample_size=3,
        average_rating_in_sample=decimal.Decimal("4.00"),
        summary={"overall": "Old", "kind": "summary"},
        created_at=stale_review_time,
    )
    db_session.add(stale_rev)

    # Fresh review summary
    fresh_rev = ReviewSummary(
        id=uuid.uuid4(),
        listing_id=listing_id,
        reviews_hash="b" * 64,
        sample_size=3,
        average_rating_in_sample=decimal.Decimal("4.50"),
        summary={"overall": "Fresh", "kind": "summary"},
        created_at=fresh_review_time,
    )
    db_session.add(fresh_rev)
    stale_session_id = stale_session.id
    stale_ctx_id = stale_ctx.id
    fresh_session_id = fresh_session.id
    stale_rev_id = stale_rev.id
    fresh_rev_id = fresh_rev.id

    db_session.commit()

    # Run cleanup job
    headers = {"X-Job-Secret": settings.JOB_SECRET}
    job_res = client.post("/internal/jobs/run", headers=headers)
    assert job_res.status_code == 200
    res_data = job_res.json()
    assert res_data["deleted_sessions"] >= 1
    assert res_data["deleted_review_summaries"] >= 1

    # Verify stale session is deleted, fresh session survives
    db_session.expire_all()
    assert db_session.get(ChatSession, stale_session_id) is None
    assert db_session.get(ShoppingContext, stale_ctx_id) is None
    assert db_session.get(ChatSession, fresh_session_id) is not None

    # Verify stale review summary is deleted, fresh survives
    assert db_session.get(ReviewSummary, stale_rev_id) is None
    assert db_session.get(ReviewSummary, fresh_rev_id) is not None

    # Idempotent second run
    job_res2 = client.post("/internal/jobs/run", headers=headers)
    assert job_res2.status_code == 200
    assert job_res2.json()["deleted_sessions"] == 0
    assert job_res2.json()["deleted_review_summaries"] == 0
