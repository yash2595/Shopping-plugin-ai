import decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import ToolProposal
from app.models.context import RejectedProduct, ShoppingContext
from app.models.product import ProductSnapshot
from app.models.session import Session as ChatSession
from app.policies.proposal_policy import ProposalPolicyEngine
from app.services.auth_service import AuthService


def test_context_created_with_session(client: TestClient, auth_headers: dict[str, str], db_session: Session):
    """Proves that a shopping context row is automatically created together with every new session."""
    r = client.post("/v1/sessions", json={"title": "Test Session"}, headers=auth_headers)
    assert r.status_code == 201
    session_id = r.json()["id"]

    ctx_resp = client.get(f"/v1/sessions/{session_id}/context", headers=auth_headers)
    assert ctx_resp.status_code == 200
    ctx = ctx_resp.json()
    assert ctx["session_id"] == session_id
    assert ctx["budget_max"] is None
    assert ctx["currency"] == "INR"
    assert ctx["preferences"] == {}
    assert ctx["version"] == 1
    assert ctx["rejections"] == []


def test_context_patch_merges_and_deletes_keys(client: TestClient, auth_headers: dict[str, str]):
    """Proves that PATCH /v1/sessions/{id}/context merges preferences and null value deletes a key."""
    s_resp = client.post("/v1/sessions", json={"title": "Pref Session"}, headers=auth_headers)
    session_id = s_resp.json()["id"]

    # Initial set: color and brand
    p1 = client.patch(
        f"/v1/sessions/{session_id}/context",
        json={"version": 1, "budget_max": "3000.00", "preferences": {"color": "black", "brand": "sony"}},
        headers=auth_headers,
    )
    assert p1.status_code == 200
    data1 = p1.json()
    assert data1["budget_max"] == "3000.00"
    assert data1["preferences"] == {"color": "black", "brand": "sony"}
    assert data1["version"] == 2

    # Second set: update color, add category, delete brand via null
    p2 = client.patch(
        f"/v1/sessions/{session_id}/context",
        json={"version": 2, "preferences": {"color": "silver", "brand": None, "category": "audio"}},
        headers=auth_headers,
    )
    assert p2.status_code == 200
    data2 = p2.json()
    assert data2["budget_max"] == "3000.00"  # Unchanged
    assert data2["preferences"] == {"color": "silver", "category": "audio"}
    assert "brand" not in data2["preferences"]
    assert data2["version"] == 3


def test_context_patch_stale_version_409(client: TestClient, auth_headers: dict[str, str]):
    """Proves optimistic locking: providing a stale version returns 409 VERSION_CONFLICT."""
    s_resp = client.post("/v1/sessions", headers=auth_headers)
    session_id = s_resp.json()["id"]

    # Valid update
    p1 = client.patch(
        f"/v1/sessions/{session_id}/context",
        json={"version": 1, "budget_max": "5000.00"},
        headers=auth_headers,
    )
    assert p1.status_code == 200

    # Stale version update (version is now 2, submitting version 1)
    p2 = client.patch(
        f"/v1/sessions/{session_id}/context",
        json={"version": 1, "budget_max": "4000.00"},
        headers=auth_headers,
    )
    assert p2.status_code == 409
    body = p2.json()
    assert body["error"]["code"] == "VERSION_CONFLICT"


def test_context_patch_unknown_preference_key_422(client: TestClient, auth_headers: dict[str, str]):
    """Proves that unknown preference keys outside whitelist are rejected with 422 INVALID_PREFERENCE_KEY."""
    s_resp = client.post("/v1/sessions", headers=auth_headers)
    session_id = s_resp.json()["id"]

    res = client.patch(
        f"/v1/sessions/{session_id}/context",
        json={"version": 1, "preferences": {"fabric": "cotton"}},
        headers=auth_headers,
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "INVALID_PREFERENCE_KEY"


def test_context_another_user_session_returns_404(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    """Proves user data isolation: accessing another user's session context returns 404."""
    auth_service = AuthService()
    t2, _ = auth_service.register_anonymous_user(db_session)
    h2 = {"Authorization": f"Bearer {t2}"}

    s_resp = client.post("/v1/sessions", headers=auth_headers)
    session_id = s_resp.json()["id"]

    # User 2 tries to GET User 1's context
    g = client.get(f"/v1/sessions/{session_id}/context", headers=h2)
    assert g.status_code == 404

    # User 2 tries to PATCH User 1's context
    p = client.patch(f"/v1/sessions/{session_id}/context", json={"version": 1, "budget_max": "100.00"}, headers=h2)
    assert p.status_code == 404


def test_rejection_duplicate_idempotent_and_unseen_listing_409(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    """Proves product rejection requires seen snapshot (else 409), and duplicate rejections are idempotent 200."""
    s_resp = client.post("/v1/sessions", headers=auth_headers)
    session_id = s_resp.json()["id"]

    # Create listing via snapshot
    snap_resp = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": f"p_{uuid.uuid4().hex[:6]}",
            "title": "Noise Cancelling Headphones",
            "price": "2999.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    )
    assert snap_resp.status_code == 201
    listing_id = snap_resp.json()["listing_id"]

    # User 2 tries to reject without having captured snapshot -> 409 LISTING_NOT_SEEN
    auth_service = AuthService()
    t2, _ = auth_service.register_anonymous_user(db_session)
    h2 = {"Authorization": f"Bearer {t2}"}
    s2 = client.post("/v1/sessions", headers=h2).json()["id"]

    r_fail = client.post(
        f"/v1/sessions/{s2}/context/rejections",
        json={"listing_id": listing_id, "reason": "Too expensive"},
        headers=h2,
    )
    assert r_fail.status_code == 409
    assert r_fail.json()["error"]["code"] == "LISTING_NOT_SEEN"

    # User 1 rejects -> 200 OK
    r1 = client.post(
        f"/v1/sessions/{session_id}/context/rejections",
        json={"listing_id": listing_id, "reason": "Too bulky"},
        headers=auth_headers,
    )
    assert r1.status_code == 200
    assert r1.json()["listing_id"] == listing_id
    assert r1.json()["reason"] == "Too bulky"

    # Duplicate rejection by User 1 -> idempotent 200 OK
    r2 = client.post(
        f"/v1/sessions/{session_id}/context/rejections",
        json={"listing_id": listing_id, "reason": "Too bulky"},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]

    # DELETE rejection -> 204
    d = client.delete(f"/v1/sessions/{session_id}/context/rejections/{listing_id}", headers=auth_headers)
    assert d.status_code == 204

    # GET context -> rejections list is now empty
    ctx = client.get(f"/v1/sessions/{session_id}/context", headers=auth_headers).json()
    assert len(ctx["rejections"]) == 0


def test_carry_context_from_copies_and_refuses_another_user_session(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    """Proves carry_context_from copies budget, preferences, and rejections, and rejects another user's session with 404."""
    # Session 1 with budget, preferences, and rejection
    s1 = client.post("/v1/sessions", json={"title": "Session 1"}, headers=auth_headers).json()["id"]
    client.patch(
        f"/v1/sessions/{s1}/context",
        json={"version": 1, "budget_max": "3500.00", "preferences": {"color": "blue", "size": "M"}},
        headers=auth_headers,
    )

    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": f"p_{uuid.uuid4().hex[:6]}",
            "title": "Blue T-Shirt",
            "price": "999.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    ).json()
    listing_id = snap["listing_id"]

    client.post(
        f"/v1/sessions/{s1}/context/rejections",
        json={"listing_id": listing_id, "reason": "Wrong shade"},
        headers=auth_headers,
    )

    # Session 2 carrying from Session 1
    s2 = client.post(
        "/v1/sessions",
        json={"title": "Session 2", "carry_context_from": s1},
        headers=auth_headers,
    )
    assert s2.status_code == 201
    s2_id = s2.json()["id"]

    # Verify context in Session 2 was cloned
    c2 = client.get(f"/v1/sessions/{s2_id}/context", headers=auth_headers).json()
    assert c2["budget_max"] == "3500.00"
    assert c2["preferences"] == {"color": "blue", "size": "M"}
    assert len(c2["rejections"]) == 1
    assert c2["rejections"][0]["listing_id"] == listing_id

    # User 2 tries to carry from User 1's session -> 404
    auth_service = AuthService()
    t2, _ = auth_service.register_anonymous_user(db_session)
    h2 = {"Authorization": f"Bearer {t2}"}

    s_fail = client.post(
        "/v1/sessions",
        json={"title": "Stranger Session", "carry_context_from": s1},
        headers=h2,
    )
    assert s_fail.status_code == 404


def test_budget_tool_via_chat_executes_once_and_replay_does_not_repeat_it(
    client: TestClient,
    auth_headers: dict[str, str],
):
    """Proves 'my budget is 3000' in chat updates context budget_max, and replay returns stored reply with header."""
    s_resp = client.post("/v1/sessions", headers=auth_headers)
    session_id = s_resp.json()["id"]

    req_id = str(uuid.uuid4())
    msg_resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"text": "my budget is 3000", "request_id": req_id},
        headers=auth_headers,
    )
    assert msg_resp.status_code == 200
    assert "Idempotent-Replay" not in msg_resp.headers
    msg_data = msg_resp.json()
    assert msg_data["kind"] == "tool"

    # Context budget must be updated to 3000.00
    ctx = client.get(f"/v1/sessions/{session_id}/context", headers=auth_headers).json()
    assert ctx["budget_max"] == "3000.00"
    assert ctx["version"] == 2

    # Replay with same request_id
    replay_resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"text": "my budget is 3000", "request_id": req_id},
        headers=auth_headers,
    )
    assert replay_resp.status_code == 200
    assert replay_resp.headers.get("Idempotent-Replay") == "true"
    assert replay_resp.json()["id"] == msg_data["id"]

    # Context version remains 2 (did not execute again)
    ctx2 = client.get(f"/v1/sessions/{session_id}/context", headers=auth_headers).json()
    assert ctx2["version"] == 2


def test_preference_and_rejection_tools_via_chat(
    client: TestClient,
    auth_headers: dict[str, str],
):
    """Proves 'I prefer black' sets preference, and 'not this one, too expensive' rejects current snapshot."""
    s_resp = client.post("/v1/sessions", headers=auth_headers)
    session_id = s_resp.json()["id"]

    snap = client.post(
        "/v1/snapshots",
        json={
            "retailer": "amazon",
            "retailer_product_id": f"p_{uuid.uuid4().hex[:6]}",
            "title": "Sony Headphones",
            "price": "4500.00",
            "availability": "in_stock",
        },
        headers=auth_headers,
    ).json()

    # Preference tool
    r_pref = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"text": "I prefer black", "request_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert r_pref.status_code == 200
    assert r_pref.json()["kind"] == "tool"

    ctx = client.get(f"/v1/sessions/{session_id}/context", headers=auth_headers).json()
    assert ctx["preferences"].get("color") == "black"

    # Rejection tool
    r_rej = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "text": "not this one, too expensive",
            "snapshot_id": snap["snapshot_id"],
            "request_id": str(uuid.uuid4()),
        },
        headers=auth_headers,
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["kind"] == "tool"

    ctx2 = client.get(f"/v1/sessions/{session_id}/context", headers=auth_headers).json()
    assert len(ctx2["rejections"]) == 1
    assert ctx2["rejections"][0]["listing_id"] == snap["listing_id"]


def test_policy_rejects_budget_less_than_zero_and_non_whitelist_key(db_session: Session):
    """Proves policy engine rejects budget <= 0 and non-whitelisted keys."""
    from app.models.user import User
    auth_service = AuthService()
    t, _ = auth_service.register_anonymous_user(db_session)
    user = auth_service.authenticate_token(db_session, t)

    policy = ProposalPolicyEngine()

    # Budget <= 0
    res_b = policy.validate_proposal(
        proposal=ToolProposal(tool="set_budget", args={"budget_max": -50}),
        user=user,
        context={},
        db=db_session,
    )
    assert res_b.action == "rejected"

    # Non-whitelisted key
    res_p = policy.validate_proposal(
        proposal=ToolProposal(tool="set_preference", args={"key": "unsupported_key", "value": "val"}),
        user=user,
        context={},
        db=db_session,
    )
    assert res_p.action == "rejected"
