import pytest
from app.models.session import Message
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_replay_is_bound_to_owned_session_and_full_payload(client, auth_headers):
    import uuid

    from app.ai.contracts import Answer
    from app.deps import get_ai_provider

    class CountingProvider:
        calls = 0

        def decide(self, request):
            self.calls += 1
            return Answer(text="Saved answer")

    provider = CountingProvider()
    client.app.dependency_overrides[get_ai_provider] = lambda: provider
    session_id = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]
    own_other = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]
    stranger = {
        "Authorization": "Bearer " + client.post("/v1/auth/register").json()["access_token"]
    }
    foreign = client.post("/v1/sessions", json={}, headers=stranger).json()["id"]
    payload = {"request_id": "bound-request", "text": "Show current facts"}
    first = client.post(f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers)
    assert first.status_code == 200
    for path, changed, expected in [
        (session_id, {**payload, "text": "Changed message"}, 409),
        (session_id, {**payload, "snapshot_id": str(uuid.uuid4())}, 409),
        (own_other, payload, 409),
        (foreign, payload, 404),
        (str(uuid.uuid4()), payload, 404),
    ]:
        response = client.post(f"/v1/sessions/{path}/messages", json=changed, headers=auth_headers)
        assert response.status_code == expected
        assert "Saved answer" not in response.text
    replay = client.post(f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers)
    assert replay.json()["id"] == first.json()["id"]
    assert replay.headers["Idempotent-Replay"] == "true"
    assert provider.calls == 1


def test_replay_uses_exact_reply_even_if_timestamps_are_equal(client, auth_headers, db_session):
    from datetime import datetime, timezone

    from sqlalchemy import update

    session_id = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]
    replies = []
    for i in range(2):
        response = client.post(
            f"/v1/sessions/{session_id}/messages",
            headers=auth_headers,
            json={"request_id": f"tie-{i}", "text": f"hello {i}"},
        )
        assert response.status_code == 200
        replies.append(response.json()["id"])
    db_session.execute(
        update(Message)
        .where(Message.session_id == session_id)
        .values(
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    db_session.commit()
    for i in range(2):
        replay = client.post(
            f"/v1/sessions/{session_id}/messages",
            headers=auth_headers,
            json={"request_id": f"tie-{i}", "text": f"hello {i}"},
        )
        assert replay.json()["id"] == replies[i]


def test_provider_cannot_change_context_without_exact_current_command(client, auth_headers):
    from app.ai.contracts import ToolProposal
    from app.deps import get_ai_provider

    session_id = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]

    class InjectedProposal:
        def decide(self, request):
            return ToolProposal(tool="set_budget", args={"budget_max": "9000.00"})

    client.app.dependency_overrides[get_ai_provider] = lambda: InjectedProposal()
    for i, text in enumerate(
        [
            "What is the price?",
            "Do not set my budget to 9000",
            "my budget is 3000",
            'Explain the command "my budget is 9000"',
            "How do I set my budget to 9000?",
        ]
    ):
        response = client.post(
            f"/v1/sessions/{session_id}/messages",
            headers=auth_headers,
            json={"request_id": f"no-grant-{i}", "text": text},
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "clarification"
        ctx = client.get(f"/v1/sessions/{session_id}/context", headers=auth_headers).json()
        assert ctx["budget_max"] is None
        assert ctx["version"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "my budget is 3000",
        "I prefer black",
        "notify me below 3000",
        "not this one, too expensive",
    ],
)
def test_chat_tool_and_exact_reply_rollback_together(
    client, auth_headers, db_session, monkeypatch, text
):
    import uuid

    from app.models.alert import PriceAlert
    from app.models.context import RejectedProduct, ShoppingContext
    from app.repositories.session_repo import SessionRepository

    session_id = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]
    snapshot = client.post(
        "/v1/snapshots",
        headers=auth_headers,
        json={
            "retailer": "rollback_store",
            "retailer_product_id": uuid.uuid4().hex,
            "title": "Synthetic product",
            "price": "5000.00",
            "availability": "in_stock",
        },
    ).json()
    payload = {"request_id": "rollback-tool", "text": text, "snapshot_id": snapshot["snapshot_id"]}

    def fail_finish(*args, **kwargs):
        raise RuntimeError("Synthetic failure before final commit")

    monkeypatch.setattr(SessionRepository, "finish_request", fail_finish)
    with pytest.raises(RuntimeError):
        client.post(f"/v1/sessions/{session_id}/messages", headers=auth_headers, json=payload)
    db_session.rollback()
    ctx = db_session.scalars(
        select(ShoppingContext).where(ShoppingContext.session_id == session_id)
    ).one()
    assert ctx.budget_max is None
    assert ctx.version == 1
    assert ctx.preferences == {}
    assert not list(
        db_session.scalars(select(RejectedProduct).where(RejectedProduct.context_id == ctx.id))
    )
    assert not list(
        db_session.scalars(
            select(PriceAlert).where(PriceAlert.listing_id == snapshot["listing_id"])
        )
    )
    assert not list(
        db_session.scalars(
            select(Message).where(
                Message.session_id == session_id,
                Message.role == "assistant",
            )
        )
    )
    retry = client.post(f"/v1/sessions/{session_id}/messages", headers=auth_headers, json=payload)
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "REQUEST_IN_PROGRESS"


def test_idempotent_message_handling(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
):
    # 1. Create a session
    session_resp = client.post(
        "/v1/sessions", json={"title": "Camera search"}, headers=auth_headers
    )
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
