import pytest
from sqlalchemy.engine.url import make_url


def test_refuse_run_on_non_test_database():
    unsafe_urls = [
        "postgresql+psycopg://postgres@127.0.0.1:5433/shopping",
        "postgresql+psycopg://postgres@127.0.0.1:5433/production",
        "postgresql+psycopg://postgres@127.0.0.1:5433/test_db",
        "postgresql+psycopg://postgres@127.0.0.1:5433/shopping_test_extra",
    ]
    for url in unsafe_urls:
        db_name = make_url(url).database
        assert not db_name.endswith("_test"), f"URL unexpectedly ended with _test: {url}"

    safe_url = "postgresql+psycopg://postgres@127.0.0.1:5433/shopping_test"
    safe_db_name = make_url(safe_url).database
    assert safe_db_name.endswith("_test")


@pytest.mark.parametrize(
    "body",
    [
        '{"text":"a","text":"b"}',
        '{"price":NaN}',
        '{"price":Infinity}',
        '{"price":1e999}',
        '{"text":"\\ud800"}',
        '{"text":"\\u0000"}',
        '{"preferences":' + "[" * 40 + "0" + "]" * 40 + "}",
        '{"text":"' + "x" * 270_000 + '"}',
    ],
)
def test_invalid_or_unbounded_json_rejected_before_route(client, body):
    response = client.post(
        "/v1/auth/register", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code in {413, 422}
    assert "access_token" not in response.text


def test_non_json_request_and_oversized_chat_text_rejected(client, auth_headers):
    assert client.post("/v1/auth/register", content="not-json").status_code == 415
    session_id = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]
    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        headers=auth_headers,
        json={"request_id": "oversized", "text": "x" * 2001},
    )
    assert response.status_code == 422


def test_body_limit_applies_without_content_length(client):
    def chunks():
        yield b'{"text":"'
        for _ in range(40):
            yield b"x" * 8192
        yield b'"}'

    response = client.post(
        "/v1/auth/register", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_errors_and_logs_do_not_echo_private_exception_or_validation_input(client, caplog):
    from app.main import create_app
    from fastapi.testclient import TestClient

    marker = "PRIVATE_USER_TEXT_AND_SECRET"
    rejected = client.post("/v1/sessions", json={marker: marker})
    assert marker not in rejected.text
    app = create_app()

    @app.get("/synthetic-error")
    def fail():
        raise RuntimeError(marker)

    with TestClient(app, raise_server_exceptions=False) as local:
        response = local.get("/synthetic-error")
    assert response.status_code == 500
    assert response.headers["X-Request-ID"]
    assert marker not in response.text
    assert marker not in caplog.text


def test_production_refuses_default_maintenance_secret():
    from app.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(ENV="production", JOB_SECRET="dev-job-secret-token", _env_file=None)
    assert Settings(ENV="production", JOB_SECRET="x" * 40, _env_file=None).ENV == "production"
