from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.auth_service import AuthService


def test_create_and_get_own_session(client: TestClient, auth_headers: dict[str, str]):
    # Create session
    create_resp = client.post("/v1/sessions", json={"title": "Shopping for headphones"}, headers=auth_headers)
    assert create_resp.status_code == 201
    session_data = create_resp.json()
    assert "id" in session_data
    assert session_data["title"] == "Shopping for headphones"

    # Get own session -> 200
    session_id = session_data["id"]
    get_resp = client.get(f"/v1/sessions/{session_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["id"] == session_id
    assert get_data["title"] == "Shopping for headphones"
    assert "messages" in get_data


def test_get_another_users_session_returns_404(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # User 1 creates a session
    resp1 = client.post("/v1/sessions", json={"title": "User 1 session"}, headers=auth_headers)
    assert resp1.status_code == 201
    user1_session_id = resp1.json()["id"]

    # User 2 registers and tries to access User 1's session
    auth_service = AuthService()
    user2_token, _ = auth_service.register_anonymous_user(db_session)
    user2_headers = {"Authorization": f"Bearer {user2_token}"}

    resp2 = client.get(f"/v1/sessions/{user1_session_id}", headers=user2_headers)
    # Must return 404 (not 403)
    assert resp2.status_code == 404
    assert resp2.json()["error"]["code"] == "not_found"
    assert resp2.json()["error"]["message"] == "Session not found"


def test_rejects_unknown_fields(client: TestClient, auth_headers: dict[str, str]):
    # Pydantic extra='forbid' validation
    resp = client.post(
        "/v1/sessions",
        json={"title": "Test", "unknown_field": "disallowed"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
