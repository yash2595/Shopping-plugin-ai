import decimal
import uuid
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import AIProvider, AIProviderError, AIRequest, Answer, ToolProposal
from app.deps import get_ai_provider
from app.models.alert import PriceAlert
from app.models.product import Listing
from app.services.auth_service import AuthService


def test_mock_answer_path(client: TestClient, auth_headers: dict[str, str]):
    # 1. Create session
    sess_resp = client.post("/v1/sessions", json={"title": "Deal Search"}, headers=auth_headers)
    session_id = sess_resp.json()["id"]

    # 2. Capture snapshot
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": f"DP_{uuid.uuid4().hex[:8].upper()}",
            "title": "Noise Cancelling Headphones",
            "url": "https://amazon.com/dp/headphones",
            "price": "2500.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    snap_id = snap_resp.json()["snapshot_id"]

    # 3. Post user message asking if it's a good deal
    msg_resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "request_id": f"req_{uuid.uuid4().hex}",
            "snapshot_id": snap_id,
            "text": "Is this a good deal?",
        },
        headers=auth_headers,
    )
    assert msg_resp.status_code == 200
    reply = msg_resp.json()
    assert reply["role"] == "assistant"
    assert reply["kind"] == "answer"
    assert "no price history yet" in reply["content"].lower()
    assert "2500.00" in reply["content"]


def test_clarification_path(client: TestClient, auth_headers: dict[str, str]):
    sess_resp = client.post("/v1/sessions", json={"title": "Alert Setup"}, headers=auth_headers)
    session_id = sess_resp.json()["id"]

    # User says "notify me" with no price
    msg_resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "request_id": f"req_{uuid.uuid4().hex}",
            "text": "notify me",
        },
        headers=auth_headers,
    )
    assert msg_resp.status_code == 200
    reply = msg_resp.json()
    assert reply["role"] == "assistant"
    assert reply["kind"] == "clarification"
    assert "target price" in reply["content"].lower()


def test_tool_proposal_creates_exactly_one_alert_and_replays_idempotently(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    sess_resp = client.post("/v1/sessions", json={"title": "Alert Automation"}, headers=auth_headers)
    session_id = sess_resp.json()["id"]

    # Snapshot at 3500.00
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": f"AL_{uuid.uuid4().hex[:8].upper()}",
            "title": "Smart Watch",
            "url": "https://amazon.com/dp/watch",
            "price": "3500.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    snap_id = snap_resp.json()["snapshot_id"]
    listing_id = snap_resp.json()["listing_id"]

    request_id = f"req_tool_{uuid.uuid4().hex}"
    payload = {
        "request_id": request_id,
        "snapshot_id": snap_id,
        "text": "notify me below 3000",
    }

    # 1. First execution
    resp1 = client.post(f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers)
    assert resp1.status_code == 200
    assert "Idempotent-Replay" not in resp1.headers
    data1 = resp1.json()
    assert data1["kind"] == "tool"
    assert data1["alert"] is not None
    assert decimal.Decimal(data1["alert"]["target_price"]) == decimal.Decimal("3000.00")
    created_alert_id = data1["alert"]["id"]

    # Verify DB has exactly 1 alert
    alerts_in_db = list(
        db_session.scalars(select(PriceAlert).where(PriceAlert.listing_id == uuid.UUID(listing_id))).all()
    )
    assert len(alerts_in_db) == 1
    assert str(alerts_in_db[0].id) == created_alert_id

    # 2. Replay with the same request_id
    resp2 = client.post(f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers)
    assert resp2.status_code == 200
    assert resp2.headers.get("Idempotent-Replay") == "true"
    data2 = resp2.json()
    assert data2["id"] == data1["id"]
    assert data2["alert"]["id"] == created_alert_id

    # Verify tool did NOT run again: still exactly 1 alert
    alerts_after_replay = list(
        db_session.scalars(select(PriceAlert).where(PriceAlert.listing_id == uuid.UUID(listing_id))).all()
    )
    assert len(alerts_after_replay) == 1


def test_policy_rejects_target_greater_than_or_equal_to_current_price(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    sess_resp = client.post("/v1/sessions", json={"title": "Policy Bounds"}, headers=auth_headers)
    session_id = sess_resp.json()["id"]

    # Snapshot at 2000.00
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "bestbuy",
            "retailer_product_id": f"PB_{uuid.uuid4().hex[:8]}",
            "title": "Camera",
            "url": "https://bestbuy.com/cam",
            "price": "2000.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    snap_id = snap_resp.json()["snapshot_id"]
    listing_id = snap_resp.json()["listing_id"]

    # Request alert at 2500.00 (> current price)
    resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "request_id": f"req_target_high_{uuid.uuid4().hex}",
            "snapshot_id": snap_id,
            "text": "notify me below 2500",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "answer"
    assert "must be below the current price" in data["content"].lower()

    # Verify no alert was created
    alerts = list(db_session.scalars(select(PriceAlert).where(PriceAlert.listing_id == uuid.UUID(listing_id))).all())
    assert len(alerts) == 0


def test_policy_rejects_21st_alert(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    sess_resp = client.post("/v1/sessions", json={"title": "Max Alerts Test"}, headers=auth_headers)
    session_id = sess_resp.json()["id"]

    # Pre-populate 20 active alerts for this user
    user_resp = client.get("/v1/alerts", headers=auth_headers)
    assert user_resp.status_code == 200

    from app.services.auth_service import AuthService
    auth_s = AuthService()
    token = auth_headers["Authorization"].split(" ")[1]
    user = auth_s.authenticate_token(db_session, token)

    # Insert 20 active alerts
    for i in range(20):
        listing = Listing(
            retailer="bestbuy",
            retailer_product_id=f"LIMIT_{uuid.uuid4().hex[:8]}",
            title=f"Item {i}",
        )
        db_session.add(listing)
        db_session.flush()
        db_session.add(
            PriceAlert(
                user_id=user.id,
                listing_id=listing.id,
                target_price=decimal.Decimal("50.00"),
                status="active",
                arm_count=1,
                version=1,
            )
        )
    db_session.commit()

    # Capture 21st snapshot
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "bestbuy",
            "retailer_product_id": f"ITEM_21_{uuid.uuid4().hex[:8]}",
            "title": "21st Item",
            "url": "https://bestbuy.com/21",
            "price": "100.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    snap_id = snap_resp.json()["snapshot_id"]

    # Attempt to create 21st alert via copilot
    resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "request_id": f"req_alert_21_{uuid.uuid4().hex}",
            "snapshot_id": snap_id,
            "text": "notify me below 80",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "answer"
    assert "active alert limit" in data["content"].lower()


def test_provider_error_returns_fallback_reply(
    client: TestClient,
    auth_headers: dict[str, str],
):
    sess_resp = client.post("/v1/sessions", json={"title": "Error Resilience"}, headers=auth_headers)
    session_id = sess_resp.json()["id"]

    # "crash" triggers MockProvider to raise AIProviderError
    resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "request_id": f"req_crash_{uuid.uuid4().hex}",
            "text": "please crash now",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "fallback"
    assert "having trouble" in data["content"].lower()


def test_provider_timeout_returns_fallback_reply_http_200(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    import time
    from app.config import get_settings
    settings = get_settings()
    # Set short timeout for test
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 0.05)

    class HangingProvider(AIProvider):
        def decide(self, request: AIRequest):
            time.sleep(0.3)
            return Answer(text="Finished too late")

    target_app = client.app if hasattr(client, "app") else app
    target_app.dependency_overrides[get_ai_provider] = lambda: HangingProvider()

    try:
        sess_resp = client.post("/v1/sessions", json={"title": "Timeout Resilience"}, headers=auth_headers)
        session_id = sess_resp.json()["id"]

        resp = client.post(
            f"/v1/sessions/{session_id}/messages",
            json={
                "request_id": f"req_timeout_{uuid.uuid4().hex}",
                "text": "What is the best deal?",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "fallback"
        assert data["role"] == "assistant"
        assert "having trouble" in data["content"].lower()
    finally:
        target_app.dependency_overrides.pop(get_ai_provider, None)


def test_hallucinated_amount_replaced_by_templated_answer(
    client: TestClient,
    auth_headers: dict[str, str],
):
    sess_resp = client.post("/v1/sessions", json={"title": "Grounding Check"}, headers=auth_headers)
    session_id = sess_resp.json()["id"]

    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "target",
            "retailer_product_id": f"GR_{uuid.uuid4().hex[:8]}",
            "title": "Blender",
            "url": "https://target.com/blender",
            "price": "3499.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    snap_id = snap_resp.json()["snapshot_id"]

    # Custom test provider that returns a hallucinated price ₹1,999
    class HallucinatingProvider(AIProvider):
        def decide(self, request: AIRequest):
            return Answer(text="This blender is on sale for only ₹1,999! Grab it now.")

    from app.main import app
    app.dependency_overrides[get_ai_provider] = lambda: HallucinatingProvider()

    try:
        resp = client.post(
            f"/v1/sessions/{session_id}/messages",
            json={
                "request_id": f"req_hallucinate_{uuid.uuid4().hex}",
                "snapshot_id": snap_id,
                "text": "What do you think of this price?",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "answer"
        # The hallucinated amount ₹1,999 MUST NOT be present
        assert "1,999" not in data["content"]
        assert "1999" not in data["content"]
        # Must be replaced by templated factual answer
        assert "3499.00" in data["content"]
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)


def test_another_user_snapshot_id_returns_404(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # User 1 captures snapshot
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "bestbuy",
            "retailer_product_id": f"PRIV_{uuid.uuid4().hex[:8]}",
            "title": "Private Snapshot Product",
            "url": "https://bestbuy.com/priv",
            "price": "199.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap_resp.status_code == 201
    user1_snapshot_id = snap_resp.json()["snapshot_id"]

    # User 2 registers and creates their own session
    auth_s = AuthService()
    user2_token, _ = auth_s.register_anonymous_user(db_session)
    user2_headers = {"Authorization": f"Bearer {user2_token}"}

    sess2_resp = client.post("/v1/sessions", json={"title": "User 2 Session"}, headers=user2_headers)
    session2_id = sess2_resp.json()["id"]

    # User 2 attempts to use User 1's snapshot_id
    resp = client.post(
        f"/v1/sessions/{session2_id}/messages",
        json={
            "request_id": f"req_leak_{uuid.uuid4().hex}",
            "snapshot_id": user1_snapshot_id,
            "text": "Check this deal",
        },
        headers=user2_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert resp.json()["error"]["message"] == "Snapshot not found"


def test_gemini_provider_structured_json_with_mocked_client():
    from app.ai.gemini import GeminiProvider
    mock_genai_client = MagicMock()
    mock_models = MagicMock()
    mock_genai_client.models = mock_models

    # 1. Mock Answer
    mock_models.generate_content.return_value = MagicMock(
        text='{"text": "The laptop has a good price based on facts."}'
    )
    provider = GeminiProvider(api_key="fake-test-key", client=mock_genai_client)
    decision = provider.decide(AIRequest(user_text="Is this good?"))
    assert isinstance(decision, Answer)
    assert "good price" in decision.text

    # 2. Mock ToolProposal
    mock_models.generate_content.return_value = MagicMock(
        text='{"tool": "create_price_alert", "args": {"target_price": "2499.00"}}'
    )
    decision = provider.decide(AIRequest(user_text="Notify me below 2499"))
    assert isinstance(decision, ToolProposal)
    assert decision.tool == "create_price_alert"
    assert decision.args["target_price"] == "2499.00"
