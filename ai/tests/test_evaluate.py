import asyncio

from shopping_ai.evaluate import check_setup, evaluate
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market_dialogue import MarketDraft, OfflineDetailInterpreter


def clear_config(monkeypatch):
    for name in (
        "AI_CHAT_ENDPOINT",
        "AI_CHAT_MODEL",
        "AI_CHAT_API_KEY",
        "AI_CHAT_RESPONSE_MODE",
        "AI_CHAT_TOKEN_PARAMETER",
        "AI_STT_ENDPOINT",
        "AI_STT_MODEL",
        "AI_STT_API_KEY",
        "AI_TTS_ENDPOINT",
        "AI_TTS_MODEL",
        "AI_TTS_API_KEY",
        "AI_TTS_VOICE",
        "AI_TTS_FORMAT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_preflight_lists_only_missing_variable_names(monkeypatch):
    clear_config(monkeypatch)
    monkeypatch.setenv("AI_CHAT_API_KEY", "private-test-secret")
    report = check_setup(voice=True)
    assert report.status == "configuration_incomplete"
    assert "AI_CHAT_ENDPOINT" in report.missing_variables
    assert "AI_STT_MODEL" in report.missing_variables
    assert not report.live_calls_made
    assert "private-test-secret" not in report.model_dump_json()


def test_preflight_rejects_invalid_endpoint_without_echoing_configuration(monkeypatch):
    clear_config(monkeypatch)
    monkeypatch.setenv("AI_CHAT_ENDPOINT", "http://private-host.invalid/path")
    monkeypatch.setenv("AI_CHAT_MODEL", "model")
    report = check_setup()
    assert report.issues == ("INVALID_CHAT_CONFIGURATION",)
    assert "private-host" not in report.model_dump_json()


def test_preflight_does_not_confuse_configuration_with_live_validation(monkeypatch):
    clear_config(monkeypatch)
    monkeypatch.setenv("AI_CHAT_ENDPOINT", "https://unverified.example/v1/chat/completions")
    monkeypatch.setenv("AI_CHAT_MODEL", "unverified-model")
    report = check_setup()
    assert report.status == "ready_to_attempt_live_test"
    assert report.live_calls_made is False


def test_offline_quality_report_exposes_natural_language_limitation():
    report = asyncio.run(evaluate(BasicInterpreter(), OfflineDetailInterpreter(), mode="offline"))
    assert report.passed == 9 and report.total == 10
    failed = [case for case in report.cases if not case.passed]
    assert [case.case_id for case in failed] == ["natural-details"]
    assert failed[0].outcome == "detail_mismatch"


def test_benchmark_redacts_provider_errors_and_checks_source_support():
    class BrokenIntent:
        async def interpret(self, message, awaiting):
            raise RuntimeError("SECRET_PROVIDER_BODY")

    class InventedDetail:
        async def extract(self, message, draft):
            return MarketDraft(brand="InventedBrand")

    report = asyncio.run(evaluate(BrokenIntent(), InventedDetail(), mode="test"))
    assert report.passed == 0 and report.total == 10
    assert "SECRET" not in report.model_dump_json()
    assert all(
        case.outcome == "provider_or_evidence_failure"
        for case in report.cases
        if case.task == "details"
    )
