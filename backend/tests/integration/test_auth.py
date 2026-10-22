import hashlib
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import AuthToken


def test_anonymous_registration(client: TestClient, db_session: Session):
    response = client.post("/v1/auth/register")
    assert response.status_code == 201

    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "expires_at" in data

    token = data["access_token"]
    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    # Verify that ONLY the SHA-256 hash was stored, never the raw token
    stored_token = db_session.scalars(
        select(AuthToken).where(AuthToken.token_hash == expected_hash)
    ).first()
    assert stored_token is not None
    assert stored_token.token_hash == expected_hash


def test_unauthorized_when_missing_token(client: TestClient):
    response = client.post("/v1/sessions", json={})
    assert response.status_code == 401
    assert "error" in response.json()
    assert response.json()["error"]["code"] == "unauthorized"


def test_unauthorized_when_invalid_token(client: TestClient):
    headers = {"Authorization": "Bearer invalid_nonexistent_token_123"}
    response = client.post("/v1/sessions", json={}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
