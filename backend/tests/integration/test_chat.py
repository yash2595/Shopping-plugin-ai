from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.session import Message


def test_idempotent_message_handling(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # 1. Create a session
    session_resp = client.post("/v1/sessions", json={"title": "Camera search"}, headers=auth_headers)
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    # 2. First message request with unique request_id
    payload = {
        "request_id": "req_uniq_test_1001",
        "text": "Is this a good deal?",
    }
    resp1 = client.post(f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers)
    assert resp1.status_code == 200
    assert "Idempotent-Replay" not in resp1.headers
    data1 = resp1.json()
    assert data1["role"] == "assistant"
    assert "deal" in data1["content"].lower() or "price" in data1["content"].lower()

    # Verify DB has exactly 1 message pair (1 user message, 1 assistant message)
    messages_after_1 = list(
        db_session.scalars(select(Message).where(Message.session_id == session_id)).all()
    )
    assert len(messages_after_1) == 2
    roles_1 = [m.role for m in messages_after_1]
    assert "user" in roles_1
    assert "assistant" in roles_1

    # 3. Second message request with the exact same request_id
    resp2 = client.post(f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers)
    assert resp2.status_code == 200
    assert resp2.headers.get("Idempotent-Replay") == "true"
    data2 = resp2.json()
    assert data2["id"] == data1["id"]
    assert data2["content"] == data1["content"]

    # Verify DB STILL has exactly 1 message pair (no duplicate rows inserted)
    messages_after_2 = list(
        db_session.scalars(select(Message).where(Message.session_id == session_id)).all()
    )
    assert len(messages_after_2) == 2
