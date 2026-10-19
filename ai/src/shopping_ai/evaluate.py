"""Small reproducible AI quality checks. Offline by default; live calls are opt-in.

No browser actions, persistence or speech-service requests are performed.
"""

import argparse
import asyncio
import importlib.util
import os

from pydantic import Field

from shopping_ai.intents import BasicInterpreter, Interpreter, Meaning
from shopping_ai.market_dialogue import (
    DetailInterpreter,
    MarketDraft,
    OfflineDetailInterpreter,
    validate_detail_proposal,
)
from shopping_ai.models import Contract, Identifier
from shopping_ai.providers.chat import ChatProvider, ChatSettings
from shopping_ai.providers.speech import SpeechSettings


class SetupReport(Contract):
    status: str
    missing_variables: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    live_calls_made: bool = False


def check_setup(*, voice: bool = False) -> SetupReport:
    required = ["AI_CHAT_ENDPOINT", "AI_CHAT_MODEL"]
    if voice:
        required += [
            "AI_STT_ENDPOINT",
            "AI_STT_MODEL",
            "AI_TTS_ENDPOINT",
            "AI_TTS_MODEL",
            "AI_TTS_VOICE",
        ]
    missing = tuple(name for name in required if not os.environ.get(name, "").strip())
    issues = []
    if importlib.util.find_spec("httpx") is None:
        issues.append("INSTALL_CHAT_SPEECH_EXTRAS")
    if not any(name.startswith("AI_CHAT_") for name in missing):
        try:
            ChatSettings.from_env()
        except ValueError:
            issues.append("INVALID_CHAT_CONFIGURATION")
    if voice and not any(name.startswith(("AI_STT_", "AI_TTS_")) for name in missing):
        try:
            SpeechSettings.from_env()
        except ValueError:
            issues.append("INVALID_SPEECH_CONFIGURATION")
    return SetupReport(
        status="configuration_incomplete" if missing or issues else "ready_to_attempt_live_test",
        missing_variables=missing,
        issues=tuple(issues),
    )


class CaseResult(Contract):
    case_id: Identifier
    task: str
    passed: bool
    outcome: str


class EvaluationReport(Contract):
    mode: str
    cases: tuple[CaseResult, ...]
    passed: int = Field(ge=0)
    total: int = Field(ge=1)
    scope: str = (
        "Fixed text intent/detail cases; not live browser, voice quality or load validation."
    )


INTENT_CASES = (
    ("greeting", "namaste", "greeting"),
    ("cross-store-en", "find cheapest across websites", "market_search"),
    ("cross-store-hi", "websites par sasta search kro", "market_search"),
    ("open-offer", "cheapest offer kholo", "open_offer"),
    ("explicit-compare", "compare first and second", "compare"),
    ("product-price", "price kitna hai", "product_question"),
    ("negated-cart", "cart me mat daalna", "unknown"),
    ("compound-open-buy", "open cheapest and add to cart", "unknown"),
)
EXPECTED_DETAIL = MarketDraft(
    brand="Example",
    model="Phone One",
    attributes={"storage": "128GB", "colour": "black"},
    condition="new",
    postcode="201001",
)
DETAIL_CASES = (
    (
        "labelled-details",
        "brand: Example; model: Phone One; attributes: storage=128GB,colour=black; "
        "condition: new; postcode: 201001",
    ),
    ("natural-details", "I want new Example Phone One 128GB black, delivery postcode 201001"),
)


async def evaluate(
    interpreter: Interpreter,
    details: DetailInterpreter,
    *,
    mode: str,
    timeout_seconds: float = 30,
) -> EvaluationReport:
    if not 0 < timeout_seconds <= 120:
        raise ValueError("Invalid evaluation deadline")
    cases = []
    for case_id, message, expected in INTENT_CASES:
        try:
            async with asyncio.timeout(timeout_seconds):
                raw = await interpreter.interpret(message, None)
            meaning = Meaning.model_validate(raw.model_dump(warnings=False))
            passed = meaning.intent == expected
            outcome = "matched" if passed else "intent_mismatch"
        except Exception:
            passed, outcome = False, "provider_or_schema_failure"
        cases.append(CaseResult(case_id=case_id, task="intent", passed=passed, outcome=outcome))
    for case_id, message in DETAIL_CASES:
        try:
            async with asyncio.timeout(timeout_seconds):
                raw = await details.extract(message, MarketDraft())
            proposed = validate_detail_proposal(MarketDraft(), raw, message)
            passed = proposed == EXPECTED_DETAIL
            outcome = "matched" if passed else "detail_mismatch"
        except Exception:
            passed, outcome = False, "provider_or_evidence_failure"
        cases.append(CaseResult(case_id=case_id, task="details", passed=passed, outcome=outcome))
    return EvaluationReport(
        mode=mode, cases=tuple(cases), passed=sum(c.passed for c in cases), total=len(cases)
    )


async def evaluate_scenarios() -> EvaluationReport:
    from shopping_ai.scenarios import run_scenarios

    cases = tuple(
        CaseResult(case_id=name, task="integration_scenario", passed=passed, outcome=outcome)
        for name, passed, outcome in await run_scenarios()
    )
    return EvaluationReport(
        mode="offline-scenarios",
        cases=cases,
        passed=sum(case.passed for case in cases),
        total=len(cases),
        scope="Fixed simulated runtime journeys with checkpoint codec roundtrips; "
        "not live retailer, model/speech quality, database durability or load validation.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check", action="store_true", help="Validate configuration without any network requests"
    )
    modes.add_argument(
        "--live", action="store_true", help="Run 10 model requests; may incur provider charges"
    )
    modes.add_argument(
        "--scenarios", action="store_true", help="Run fixed offline simulated runtime journeys"
    )
    parser.add_argument(
        "--voice", action="store_true", help="Also check STT/TTS config; requires --check"
    )
    args = parser.parse_args()
    if args.voice and not args.check:
        parser.error("--voice requires --check; speech quality is not evaluated by this command")
    if args.check:
        report = check_setup(voice=args.voice)
        print(report.model_dump_json(indent=2))
        raise SystemExit(0 if report.status == "ready_to_attempt_live_test" else 2)

    async def run():
        if args.scenarios:
            return await evaluate_scenarios()
        if not args.live:
            return await evaluate(BasicInterpreter(), OfflineDetailInterpreter(), mode="offline")
        setup = check_setup()
        if setup.status != "ready_to_attempt_live_test":
            print(setup.model_dump_json(indent=2))
            raise SystemExit(2)
        async with ChatProvider(ChatSettings.from_env()) as model:
            return await evaluate(model, model, mode="live")

    report = asyncio.run(run())
    print(report.model_dump_json(indent=2))
    raise SystemExit(0 if report.passed == report.total else 1)


if __name__ == "__main__":
    main()
