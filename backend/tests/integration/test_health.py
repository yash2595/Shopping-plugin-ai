from fastapi.testclient import TestClient


def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_db_health_check(client: TestClient):
    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}


def test_readiness_requires_matching_schema(client, monkeypatch):
    from app.services.health_service import HealthRepository

    assert client.get("/health/ready").json() == {"status": "ready"}
    monkeypatch.setattr(HealthRepository, "schema_ready", lambda self, db: False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Service is not ready"
    assert client.get("/health").status_code == 200


def test_capabilities_require_auth_and_exclude_secrets(client, auth_headers):
    import pytest

    from app.config import get_settings

    if not get_settings().AI_RUNTIME_ENABLED:
        pytest.skip("Optional AI runtime is disabled")
    assert client.get("/v1/ai/capabilities").status_code == 401
    response = client.get("/v1/ai/capabilities", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "mock"
    assert data["limits"]["audio_bytes"] == 5_000_000
    assert not any(
        word in response.text.lower() for word in ("api_key", "job_secret", "jwks", "grant")
    )
