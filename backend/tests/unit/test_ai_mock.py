import pytest
from app.ai.contracts import AIProviderError, AIRequest, Answer, Clarification, ToolProposal
from app.ai.mock import MockProvider


def test_mock_ai_deal_insufficient_data():
    provider = MockProvider()
    req = AIRequest(
        user_text="Is this laptop a good deal?",
        snapshot_facts={"price": "2999.00"},
        price_summary={"verdict": "insufficient_data"},
    )
    resp = provider.decide(req)

    assert isinstance(resp, Answer)
    assert "no price history yet" in resp.text.lower()
    assert "2999.00" in resp.text


def test_mock_ai_notify_creates_proposal():
    provider = MockProvider()
    req = AIRequest(user_text="notify me below 3000")
    resp = provider.decide(req)

    assert isinstance(resp, ToolProposal)
    assert resp.tool == "create_price_alert"
    assert resp.args["target_price"] == "3000"


def test_mock_ai_notify_without_number_clarifies():
    provider = MockProvider()
    req = AIRequest(user_text="notify me")
    resp = provider.decide(req)

    assert isinstance(resp, Clarification)
    assert "target price" in resp.question.lower()


def test_mock_ai_crash_raises_error():
    provider = MockProvider()
    req = AIRequest(user_text="crash now")
    with pytest.raises(AIProviderError):
        provider.decide(req)
