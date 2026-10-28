import concurrent.futures
import decimal
import uuid

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models.alert import AlertNotification, PriceAlert
from app.models.product import Listing
from app.models.user import User
from app.services.auth_service import AuthService
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

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


def test_concurrent_chat_retry_calls_provider_once_and_survives_app_restart():
    """Separate connections and apps, not the shared savepoint test fixture."""
    from threading import Event, Lock

    from app.ai.contracts import ToolProposal
    from app.deps import get_ai_provider
    from app.models.context import ShoppingContext
    from app.models.session import Message
    from app.services.session_service import SessionService

    engine = create_engine(settings.TEST_DATABASE_URL, connect_args={"prepare_threshold": None})
    entered, release = Event(), Event()
    counter_lock = Lock()
    calls, owner_id = [], None

    class BlockingProvider:
        def decide(self, request):
            with counter_lock:
                calls.append(request)
            # Every checked-out transaction must already have been released.
            # The connection pool is shared by all HTTP apps in this test.
            assert engine.pool.checkedout() == 0
            entered.set()
            assert release.wait(timeout=5)
            return ToolProposal(tool="set_budget", args={"budget_max": "3000.00"})

    def post():
        app = create_app()

        def database():
            with Session(engine) as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_ai_provider] = lambda: BlockingProvider()
        with TestClient(app) as client:
            return client.post(
                f"/v1/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"request_id": "racing-budget", "text": "my budget is 3000"},
            )

    try:
        with Session(engine) as db:
            auth = AuthService()
            token, _ = auth.register_anonymous_user(db)
            user = auth.authenticate_token(db, token)
            owner_id = user.id
            session_id = SessionService().create_session(db, user).id
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(post)
            try:
                assert entered.wait(timeout=5)
                retry = post()
                assert retry.status_code == 409
                assert retry.json()["error"]["code"] == "REQUEST_IN_PROGRESS"
            finally:
                release.set()
            completed = first.result(timeout=5)
        assert completed.status_code == 200
        assert completed.json()["kind"] == "tool"
        # post() constructs a fresh application instance each time.
        replay = post()
        assert replay.status_code == 200
        assert replay.headers["Idempotent-Replay"] == "true"
        assert replay.json()["id"] == completed.json()["id"]
        assert len(calls) == 1
        with Session(engine) as db:
            ctx = db.scalar(select(ShoppingContext).where(ShoppingContext.session_id == session_id))
            assert ctx.budget_max == decimal.Decimal("3000.00")
            assert ctx.version == 2
            messages = list(db.scalars(select(Message).where(Message.session_id == session_id)))
            assert len(messages) == 2
    finally:
        release.set()
        if owner_id:
            with Session(engine) as db:
                db.execute(delete(User).where(User.id == owner_id))
                db.commit()
        engine.dispose()
