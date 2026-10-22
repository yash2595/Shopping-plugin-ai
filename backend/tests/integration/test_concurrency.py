import concurrent.futures
import decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models.alert import AlertNotification, PriceAlert
from app.models.product import Listing
from app.models.user import User
from app.services.auth_service import AuthService

settings = get_settings()


def test_concurrent_observations_produce_single_notification():
    """
    Concurrency test:
    Two threads submitting qualifying observations at the exact same moment
    against real committed database transactions produce exactly one notification.
    """
    engine = create_engine(
        settings.TEST_DATABASE_URL,
        connect_args={"prepare_threshold": None, "client_encoding": "utf8"},
    )

    created_user_id = None
    created_listing_id = None

    try:
        # 1. Setup real committed records in test database
        with Session(engine) as db:
            from app.models.product import ProductSnapshot
            auth_service = AuthService()
            token, _ = auth_service.register_anonymous_user(db)
            user = auth_service.authenticate_token(db, token)
            assert user is not None
            created_user_id = user.id

            listing = Listing(
                retailer="concurrency_store",
                retailer_product_id=f"CONCUR_{uuid.uuid4().hex[:8]}",
                title="Race Condition Test Product",
            )
            db.add(listing)
            db.commit()
            created_listing_id = listing.id

            snapshot = ProductSnapshot(
                user_id=user.id,
                listing_id=listing.id,
                captured_price=decimal.Decimal("120.00"),
                availability="in_stock",
            )
            db.add(snapshot)
            db.commit()

            alert = PriceAlert(
                user_id=user.id,
                listing_id=listing.id,
                target_price=decimal.Decimal("100.00"),
                status="active",
                arm_count=1,
                version=1,
            )
            db.add(alert)
            db.commit()
            alert_id = alert.id

        headers = {"Authorization": f"Bearer {token}"}

        # 2. Fire two concurrent requests submitting qualifying prices against real test DB
        def send_obs(price_str: str):
            app = create_app()

            def override_get_db():
                with Session(engine) as s:
                    yield s

            app.dependency_overrides[get_db] = override_get_db

            with TestClient(app) as local_client:
                return local_client.post(
                    f"/v1/listings/{created_listing_id}/observations",
                    json={"price": price_str, "in_stock": True},
                    headers=headers,
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(send_obs, "85.00")
            fut2 = executor.submit(send_obs, "80.00")
            r1 = fut1.result()
            r2 = fut2.result()

        assert r1.status_code == 200
        assert r2.status_code == 200

        # 3. Verify exactly one notification was created and alert triggered once
        with Session(engine) as verify_db:
            notifications = list(
                verify_db.scalars(
                    select(AlertNotification).where(AlertNotification.alert_id == alert_id)
                ).all()
            )
            assert len(notifications) == 1, f"Expected 1 notification, got {len(notifications)}"

            final_alert = verify_db.get(PriceAlert, alert_id)
            assert final_alert is not None
            assert final_alert.status == "triggered"
            assert final_alert.arm_count == 1

    finally:
        # Cleanup committed records
        with Session(engine) as cleanup_db:
            if created_listing_id:
                cleanup_db.execute(delete(Listing).where(Listing.id == created_listing_id))
            if created_user_id:
                cleanup_db.execute(delete(User).where(User.id == created_user_id))
            cleanup_db.commit()
        engine.dispose()
