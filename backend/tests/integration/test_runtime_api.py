"""Offline end-to-end AI HTTP journeys backed by real PostgreSQL transactions."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

pytest.importorskip("shopping_ai")

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models.session import AIConversation, AIDispatch, AIEvent

ORIGIN = "https://demo.shop.test"
TARGET = {"tab_id": 7, "document_id": "document-1", "active": True, "window_focused": True}


def now():
    return datetime.now(timezone.utc).isoformat()


def product(**changes):
    return {
        "snapshot_id": uuid.uuid4().hex,
        "product_id": "phone-1",
        "document_id": "document-1",
        "title": "Synthetic Phone",
        "observed_at": now(),
        "selected_variant_id": "black",
        "variants": [
            {"variant_id": "black", "label": "Black"},
            {"variant_id": "blue", "label": "Blue"},
        ],
        "price": "100.10",
        "currency": "INR",
        "in_stock": True,
        **changes,
    }


@pytest.fixture
def api(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "AI_RUNTIME_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MUTATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "AI_BROWSER_ORIGINS", (ORIGIN,))
    app = create_app()

    def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        token = client.post("/v1/auth/register").json()["access_token"]
        yield Journey(client, {"Authorization": f"Bearer {token}"})


class Journey:
    def __init__(self, client, headers):
        self.client, self.headers, self.number = client, headers, 0
        self.session_id = client.post("/v1/sessions", json={}, headers=headers).json()["id"]
        self.last = client.post(
            f"/v1/ai/sessions/{self.session_id}/conversation",
            json={"language": "en"},
            headers=headers,
        ).json()
        assert "conversation_id" in self.last, self.last
        self.conversation_id = self.last["conversation_id"]
        self.base = f"/v1/ai/conversations/{self.conversation_id}"

    def post(self, suffix, payload, expected=200):
        response = self.client.post(self.base + suffix, json=payload, headers=self.headers)
        assert response.status_code == expected, response.text
        if expected == 200 and "version" in response.json() and "message" in response.json():
            self.last = response.json()
        return response

    def event(self, kind="advance", message=None, **changes):
        self.number += 1
        payload = {
            "event_id": f"event-{self.number}",
            "expected_version": self.last["version"],
            "kind": kind,
            **changes,
        }
        if message is not None:
            payload["message"] = message
        return self.post("/events", payload)

    def claim(self, action=None):
        dispatch = action or self.last["work_ids"][0]
        return self.post(
            f"/work/{dispatch}/claim",
            {
                "claim_id": "claim-" + dispatch,
                "expected_version": self.last["version"],
                "target": TARGET,
                "origin": ORIGIN,
            },
        ).json()

    def receipt(self, claim, observation):
        self.number += 1
        # Receipt time follows construction of its nested observation evidence.
        observation = {**observation, "observed_at": now()}
        payload = {
            "event_id": f"receipt-{self.number}",
            "expected_version": self.last["version"],
            "claim_token": claim["claim_token"],
            "origin": ORIGIN,
            "browser": observation,
        }
        response = self.post(f"/work/{claim['dispatch_id']}/receipts", payload)
        return response, payload

    def read_product(self, observed=None):
        self.event("text", "What is the price?", target=TARGET, origin=ORIGIN)
        claim = self.claim()
        assert claim["work"]["tool"]["name"] == "read_product"
        self.receipt(
            claim,
            {
                "outcome": "observed",
                "observed_at": now(),
                "target": TARGET,
                "product": observed or product(),
            },
        )
        self.event()
        assert self.last["status"] == "complete", self.last
        return self.last["products"][0]

    def confirm(self, observed, **changes):
        self.number += 1
        return self.post(
            "/confirmations",
            {
                "event_id": f"confirmation-{self.number}",
                "expected_version": self.last["version"],
                "confirmed": True,
                "operation": "add_to_cart",
                "snapshot_id": observed["snapshot_id"],
                "product_id": observed["product_id"],
                "variant_id": "black",
                "quantity": 1,
                "mode": "add_quantity",
                "max_unit_price": "100.10",
                "currency": "INR",
                "target": TARGET,
                "origin": ORIGIN,
                **changes,
            },
        )

    def cart_receipt(self, claim, quantity):
        lines = (
            []
            if quantity == 0
            else [{"product_id": "phone-1", "variant_id": "black", "quantity": quantity}]
        )
        return self.receipt(
            claim,
            {
                "outcome": "observed",
                "observed_at": now(),
                "target": TARGET,
                "cart_coverage": "complete",
                "cart": {"document_id": "document-1", "observed_at": now(), "lines": lines},
            },
        )


def test_complete_read_confirmation_cart_and_exact_receipt_replay(api, db_session):
    observed = api.read_product()
    assert any(fact["value"] == "INR 100.10" for fact in api.last["facts"])
    assert "state" not in api.last and "principal" not in str(api.last)
    api.confirm(observed)
    baseline = api.claim()
    assert baseline["work"]["tool"]["name"] == "read_cart"
    api.cart_receipt(baseline, 0)
    api.event()
    mutation = api.claim()
    assert mutation["work"]["tool"]["name"] == "add_to_cart"
    assert mutation["constraints"] == {"max_unit_price": "100.10", "currency": "INR"}
    response, payload = api.cart_receipt(mutation, 1)
    assert response.json()["status"] == "complete"
    replay = api.post(f"/work/{mutation['dispatch_id']}/receipts", payload)
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json() == response.json()
    assert (
        len(
            list(
                db_session.scalars(
                    select(AIDispatch).where(AIDispatch.conversation_id == api.conversation_id)
                )
            )
        )
        == 3
    )
    saved = api.client.get(api.base, headers=api.headers)
    assert saved.json() == response.json()
    assert "authorization" not in saved.text and "checkpoint" not in saved.text


def test_lost_mutation_receipt_requires_read_only_reconciliation(api):
    observed = api.read_product()
    api.confirm(observed)
    api.cart_receipt(api.claim(), 0)
    api.event()
    mutation = api.claim()
    assert mutation["work"]["tool"]["name"] == "add_to_cart"
    api.receipt(
        mutation,
        {"outcome": "unknown", "observed_at": now(), "target": TARGET, "error_code": "RESULT_LOST"},
    )
    assert api.last["status"] == "paused"
    api.event("resume", target=TARGET, origin=ORIGIN)
    api.event()
    recovery = api.claim()
    assert recovery["work"]["tool"]["name"] == "read_cart"
    api.cart_receipt(recovery, 1)
    assert api.last["status"] == "complete"
    assert api.client.get(api.base + "/work", headers=api.headers).json() == []


def test_event_replay_payload_conflict_and_forbidden_internal_fields(api):
    payload = {
        "event_id": "same-event",
        "expected_version": api.last["version"],
        "kind": "text",
        "message": "hello",
    }
    first = api.post("/events", payload)
    assert api.post("/events", payload).headers["Idempotent-Replay"] == "true"
    api.post("/events", {**payload, "message": "changed"}, expected=409)
    for field in (
        "owner_id",
        "principal",
        "authorization",
        "checkpoint",
        "available_tools",
        "provider_endpoint",
    ):
        api.post("/events", {**payload, field: {}}, expected=422)
    assert api.last == first.json()


def test_foreign_and_revoked_auth_cannot_read_claim_or_replay(api):
    token = api.client.post("/v1/auth/register").json()["access_token"]
    stranger = {"Authorization": f"Bearer {token}"}
    for suffix in ("", "/work"):
        assert api.client.get(api.base + suffix, headers=stranger).status_code == 404
    assert api.client.get(api.base).status_code == 401
    api.event("text", "price", target=TARGET, origin=ORIGIN)
    dispatch_id = api.last["work_ids"][0]
    body = {
        "claim_id": "foreign",
        "expected_version": api.last["version"],
        "target": TARGET,
        "origin": ORIGIN,
    }
    assert (
        api.client.post(
            api.base + f"/work/{dispatch_id}/claim", headers=stranger, json=body
        ).status_code
        == 404
    )
    assert api.client.post("/v1/auth/logout", headers=api.headers).status_code == 204
    assert api.client.get(api.base, headers=api.headers).status_code == 401


def test_cancel_suppresses_claim_but_records_late_bound_receipt(api):
    api.event("text", "price", target=TARGET, origin=ORIGIN)
    claim = api.claim()
    api.event("cancel")
    assert api.last["status"] == "cancelled"
    api.post(
        f"/work/{claim['dispatch_id']}/claim",
        {
            "claim_id": "claim-" + claim["dispatch_id"],
            "expected_version": api.last["version"],
            "target": TARGET,
            "origin": ORIGIN,
        },
        expected=409,
    )
    api.receipt(
        claim, {"outcome": "observed", "observed_at": now(), "target": TARGET, "product": product()}
    )
    assert api.last["status"] == "cancelled"


def test_wrong_claim_target_partial_cart_and_bad_confirmation_are_rejected(api):
    observed = api.read_product()
    api.confirm(observed)
    dispatch_id = api.last["work_ids"][0]
    api.post(
        f"/work/{dispatch_id}/claim",
        {
            "claim_id": "wrong-target",
            "expected_version": api.last["version"],
            "target": {**TARGET, "document_id": "foreign-document"},
            "origin": ORIGIN,
        },
        expected=409,
    )
    claim = api.claim()
    payload = {
        "event_id": "partial",
        "expected_version": api.last["version"],
        "origin": ORIGIN,
        "claim_token": claim["claim_token"],
        "browser": {
            "outcome": "observed",
            "observed_at": now(),
            "target": TARGET,
            "cart": {"observed_at": now(), "document_id": "document-1", "lines": []},
            "cart_coverage": "partial",
        },
    }
    api.post(f"/work/{dispatch_id}/receipts", payload, expected=422)
    api.post(
        f"/work/{dispatch_id}/receipts", {**payload, "claim_token": str(uuid.uuid4())}, expected=409
    )


def test_no_outbox_or_state_update_survives_commit_candidate_failure(api, db_session, monkeypatch):
    before = api.last["version"]
    repo = api.client.app.state.runtime_service.repo

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic outbox failure")

    monkeypatch.setattr(repo, "reconcile_outbox", fail)
    api.post(
        "/events",
        {
            "event_id": "rollback",
            "expected_version": before,
            "kind": "text",
            "message": "price",
            "target": TARGET,
            "origin": ORIGIN,
        },
        expected=503,
    )
    assert api.client.get(api.base, headers=api.headers).json()["version"] == before
    assert not list(
        db_session.scalars(
            select(AIDispatch).where(AIDispatch.conversation_id == api.conversation_id)
        )
    )
    record = db_session.get(AIEvent, (api.conversation_id, "rollback"))
    assert record.status == "failed"


def test_worker_expires_exact_pending_mutation_once_without_redispatch(api, db_session):
    observed = api.read_product()
    api.confirm(observed)
    api.cart_receipt(api.claim(), 0)
    api.event()
    claim = api.claim()
    assert claim["work"]["tool"]["name"] == "add_to_cart"
    clock = datetime.now(timezone.utc) + timedelta(seconds=45)
    svc = api.client.app.state.runtime_service
    svc.runtime.conversation.orchestrator.ai.clock = lambda: clock
    db_session.execute(
        update(AIDispatch)
        .where(AIDispatch.dispatch_id == claim["dispatch_id"])
        .values(deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    db_session.commit()
    assert asyncio.run(svc.settle_due(db_session)) == 1
    assert asyncio.run(svc.settle_due(db_session)) == 0
    summary = api.client.get(api.base, headers=api.headers).json()
    assert summary["status"] == "paused"
    assert summary["work_ids"] == []
    assert api.client.get(api.base + "/work", headers=api.headers).json() == []
    api.last = summary
    api.event("resume", target=TARGET, origin=ORIGIN)
    api.event()
    recovery = api.claim()
    assert recovery["work"]["tool"]["name"] == "read_cart"


def test_confirmation_requires_matching_fresh_observation_and_explicit_boolean(api):
    observed = api.read_product()
    base = {
        "event_id": "confirm-invalid",
        "expected_version": api.last["version"],
        "confirmed": True,
        "operation": "add_to_cart",
        "snapshot_id": observed["snapshot_id"],
        "product_id": "phone-1",
        "variant_id": "black",
        "quantity": 1,
        "mode": "add_quantity",
        "max_unit_price": "100.10",
        "currency": "INR",
        "target": TARGET,
        "origin": ORIGIN,
    }
    for patch, status in [
        ({"confirmed": 1}, 422),
        ({"confirmed": False}, 422),
        ({"snapshot_id": "foreign"}, 409),
        ({"max_unit_price": "50"}, 409),
        ({"quantity": 11}, 422),
        ({"origin": "https://unconfigured.test"}, 403),
    ]:
        api.post("/confirmations", {**base, **patch}, expected=status)
    api.client.app.state.runtime_service.runtime.conversation.orchestrator.ai.clock = lambda: (
        datetime.now(timezone.utc)
    )
    api.client.app.state.runtime_service.settings.AI_MUTATIONS_ENABLED = False
    try:
        api.post("/confirmations", base, expected=403)
    finally:
        api.client.app.state.runtime_service.settings.AI_MUTATIONS_ENABLED = True


def test_claim_is_stable_for_same_journal_id_but_not_reissued_to_a_new_claim(api):
    api.event("text", "price", target=TARGET, origin=ORIGIN)
    first = api.claim()
    assert api.claim() == first
    api.post(
        f"/work/{first['dispatch_id']}/claim",
        {
            "claim_id": "different-executor",
            "expected_version": api.last["version"],
            "target": TARGET,
            "origin": ORIGIN,
        },
        expected=409,
    )
    assert api.client.delete(api.base, headers=api.headers).status_code == 409


def test_owned_deletion_erases_private_checkpoint_events_and_outbox(api, db_session):
    api.event("text", "hello")
    assert api.client.delete(api.base, headers=api.headers).status_code == 204
    assert api.client.get(api.base, headers=api.headers).status_code == 404
    assert db_session.get(AIConversation, api.conversation_id) is None
    assert not list(
        db_session.scalars(select(AIEvent).where(AIEvent.conversation_id == api.conversation_id))
    )


def test_audio_commits_text_then_speech_retry_never_replays_stt(api, db_session):
    from shopping_ai.voice import AudioClip, Transcript, VoiceConversation

    class Speech:
        stt = 0
        tts = 0

        async def transcribe(self, clip, *, language):
            self.stt += 1
            return Transcript(text="hello", confidence=0.99)

        async def synthesize(self, text, *, language):
            self.tts += 1
            if self.tts == 1:
                raise RuntimeError("PRIVATE_PROVIDER_ERROR")
            return AudioClip(format="wav", data=b"RIFF0000WAVEsynthetic-test")

    speech = Speech()
    runtime = api.client.app.state.runtime_service.runtime
    runtime.voice = VoiceConversation(runtime.conversation, speech, speech)
    headers = {
        **api.headers,
        "Content-Type": "audio/webm",
        "X-Event-ID": "voice-1",
        "X-Expected-Version": str(api.last["version"]),
    }
    audio = b"\x1aE\xdf\xa3SYNTHETIC_AUDIO_NOT_PERSISTED"
    reply = api.client.post(api.base + "/audio", headers=headers, content=audio)
    assert reply.status_code == 200, reply.text
    assert speech.stt == 1 and speech.tts == 0
    assert (
        api.client.post(api.base + "/audio", headers=headers, content=audio).headers[
            "Idempotent-Replay"
        ]
        == "true"
    )
    assert speech.stt == 1
    version = reply.json()["version"]
    first = api.post("/speech", {"expected_version": version}, expected=503)
    assert "PRIVATE_PROVIDER" not in first.text
    second = api.client.post(
        api.base + "/speech", headers=api.headers, json={"expected_version": version}
    )
    assert second.status_code == 200
    assert second.headers["X-Conversation-Version"] == str(version)
    assert second.headers["Cache-Control"] == "no-store"
    assert speech.stt == 1 and speech.tts == 2
    assert (
        audio.decode(errors="ignore")
        not in db_session.get(AIConversation, api.conversation_id).checkpoint
    )
    api.last = reply.json()
    api.event("text", "hello")
    api.post("/speech", {"expected_version": version}, expected=409)


def test_audio_bounds_failure_and_low_confidence_never_change_checkpoint(api):
    from shopping_ai.voice import Transcript, VoiceConversation

    class Unclear:
        async def transcribe(self, clip, *, language):
            return Transcript(text="unclear", confidence=0.2)

        async def synthesize(self, *args, **kwargs):
            raise AssertionError("No TTS before a committed reply")

    runtime = api.client.app.state.runtime_service.runtime
    speech = Unclear()
    runtime.voice = VoiceConversation(runtime.conversation, speech, speech)
    version = api.last["version"]
    headers = {
        **api.headers,
        "Content-Type": "audio/webm",
        "X-Event-ID": "unclear",
        "X-Expected-Version": str(version),
    }
    assert (
        api.client.post(api.base + "/audio", headers=headers, content=b"not-audio").status_code
        == 422
    )
    assert (
        api.client.post(
            api.base + "/audio", headers=headers, content=b"\x1aE\xdf\xa3test"
        ).status_code
        == 422
    )
    assert api.client.get(api.base, headers=api.headers).json()["version"] == version
    assert (
        api.client.post(api.base + "/audio", headers=headers, content=b"x" * 5_000_001).status_code
        == 413
    )


@pytest.mark.parametrize("second_blocked", [False, True])
def test_cross_store_receipts_rank_and_navigation_use_destination_origin(api, second_blocked):
    from shopping_ai.market import Store

    runtime = api.client.app.state.runtime_service.runtime
    runtime.conversation.stores = tuple(
        Store(
            store_id=name, origin=f"https://{name}.test", search_path="/search", query_parameter="q"
        )
        for name in ("one", "two")
    )
    api.event("text", "find cheapest across websites", target=TARGET, origin=ORIGIN)
    for text in ("Example", "Phone One", "storage=128GB, colour=black", "new", "201001", "confirm"):
        api.event("text", text, target=TARGET, origin=ORIGIN)
    assert len(api.last["work_ids"]) == 2
    tasks = list(api.last["work_ids"])
    identity = {
        "brand": "Example",
        "model": "Phone One",
        "attributes": {"storage": "128GB", "colour": "black"},
        "condition": "new",
    }
    source, source_target = ORIGIN, TARGET
    offers = []
    for index, task_id in enumerate(tasks):
        claim = api.post(
            f"/work/{task_id}/claim",
            {
                "claim_id": f"search-{index}",
                "expected_version": api.last["version"],
                "target": source_target,
                "origin": source,
            },
        ).json()
        destination = claim["work"]["store"]["origin"]
        offer = {
            "offer_id": f"offer-{index}",
            "snapshot_id": f"offer-snap-{index}",
            "identity": identity,
            "url": destination + "/phone",
            "seller": "Synthetic seller",
            "observed_at": now(),
            "in_stock": True,
            "price": str(100 + index * 20),
            "currency": "INR",
            "shipping": "0",
            "extra_tax_and_fees": "0",
        }
        offers.append(offer)
        blocked = second_blocked and index == 1
        observed_target = (
            source_target if blocked else {**TARGET, "document_id": f"search-page-{index}"}
        )
        actual_origin = source if blocked else destination
        api.post(
            f"/work/{task_id}/receipts",
            {
                "event_id": f"search-receipt-{index}",
                "expected_version": api.last["version"],
                "claim_token": claim["claim_token"],
                "origin": actual_origin,
                "search": {
                    "status": "blocked" if blocked else "complete",
                    "offers": [] if blocked else [offer],
                    "target": observed_target,
                    "observed_at": now(),
                },
            },
        )
        source, source_target = actual_origin, observed_target
    assert api.last["market_result"]["cheapest_offer_ids"] == (
        [] if second_blocked else ["offer-0"]
    )
    api.event("text", "open first offer", target=source_target, origin=source)
    navigation = api.post(
        f"/work/{api.last['work_ids'][0]}/claim",
        {
            "claim_id": "open-offer",
            "expected_version": api.last["version"],
            "target": source_target,
            "origin": source,
        },
    ).json()
    destination = offers[0]["url"].rsplit("/", 1)[0]
    observed = product(price="100", document_id="opened-document")
    api.post(
        f"/work/{navigation['dispatch_id']}/receipts",
        {
            "event_id": "mismatched-capture-time",
            "expected_version": api.last["version"],
            "claim_token": navigation["claim_token"],
            "origin": destination,
            "navigation": {
                "status": "opened",
                "observed_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
                "url": offers[0]["url"],
                "target": {**TARGET, "document_id": "opened-document"},
                "identity": identity,
                "product": observed,
            },
        },
        expected=409,
    )
    api.post(
        f"/work/{navigation['dispatch_id']}/receipts",
        {
            "event_id": "opened-receipt",
            "expected_version": api.last["version"],
            "claim_token": navigation["claim_token"],
            "origin": destination,
            "navigation": {
                "status": "opened",
                "observed_at": observed["observed_at"],
                "url": offers[0]["url"],
                "target": {**TARGET, "document_id": "opened-document"},
                "identity": identity,
                "product": observed,
            },
        },
    )
    assert api.last["status"] == "complete"


@pytest.fixture
def committed_api(monkeypatch):
    """No outer savepoint: HTTP requests really use independent connections."""
    from app.models.user import User
    from app.services.auth_service import AuthService
    from sqlalchemy import create_engine, delete
    from sqlalchemy.orm import Session

    settings = get_settings()
    monkeypatch.setattr(settings, "AI_RUNTIME_ENABLED", True)
    monkeypatch.setattr(settings, "AI_BROWSER_ORIGINS", (ORIGIN,))
    engine = create_engine(settings.TEST_DATABASE_URL, connect_args={"prepare_threshold": None})

    def build_app():
        app = create_app()

        def database():
            with Session(engine) as db:
                yield db

        app.dependency_overrides[get_db] = database
        return app

    owner_id = None
    try:
        with TestClient(build_app()) as client:
            token = client.post("/v1/auth/register").json()["access_token"]
            with Session(engine) as db:
                owner_id = AuthService().authenticate_token(db, token).id
            journey = Journey(client, {"Authorization": f"Bearer {token}"})
            yield journey, engine, build_app
    finally:
        if owner_id:
            with Session(engine) as db:
                db.execute(delete(User).where(User.id == owner_id))
                db.commit()
        engine.dispose()


def blocked_provider(api, engine):
    from threading import Event

    from shopping_ai.providers.mock import MockProvider

    entered, release, calls = Event(), Event(), []

    class Blocking(MockProvider):
        async def decide(self, context):
            calls.append(context)
            if len(calls) == 1:
                assert engine.pool.checkedout() == 0, "Provider must not retain a transaction"
                entered.set()
                assert await asyncio.to_thread(release.wait, 5)
            return await super().decide(context)

    api.client.app.state.runtime_service.runtime.conversation.orchestrator.ai.provider = Blocking()
    return entered, release, calls


def test_runtime_concurrent_retry_survives_fresh_application_without_duplicate_work(committed_api):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import Session

    api, engine, build_app = committed_api
    entered, release, calls = blocked_provider(api, engine)
    payload = {
        "event_id": "racing-event",
        "expected_version": api.last["version"],
        "kind": "text",
        "message": "price",
        "target": TARGET,
        "origin": ORIGIN,
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            api.client.post, api.base + "/events", json=payload, headers=api.headers
        )
        try:
            assert entered.wait(5)
            assert (
                api.client.post(api.base + "/events", json=payload, headers=api.headers).status_code
                == 409
            )
        finally:
            release.set()
        completed = first.result(timeout=5)
    assert completed.status_code == 200, completed.text
    with TestClient(build_app()) as fresh:
        replay = fresh.post(api.base + "/events", json=payload, headers=api.headers)
        assert replay.status_code == 200
        assert replay.headers["Idempotent-Replay"] == "true"
        assert replay.json() == completed.json()
    assert len(calls) == 1
    with Session(engine) as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(AIDispatch).where(AIDispatch.conversation_id == api.conversation_id)
                    )
                )
            )
            == 1
        )


def test_cancel_overtakes_provider_and_discards_its_stale_candidate(committed_api):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import Session

    api, engine, _ = committed_api
    entered, release, _ = blocked_provider(api, engine)
    payload = {
        "event_id": "slow-event",
        "expected_version": api.last["version"],
        "kind": "text",
        "message": "price",
        "target": TARGET,
        "origin": ORIGIN,
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            api.client.post, api.base + "/events", json=payload, headers=api.headers
        )
        try:
            assert entered.wait(5)
            api.event("cancel")
            assert api.last["status"] == "cancelled"
        finally:
            release.set()
        rejected = pending.result(timeout=5)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "VERSION_CONFLICT"
    with Session(engine) as db:
        assert not list(
            db.scalars(select(AIDispatch).where(AIDispatch.conversation_id == api.conversation_id))
        )


def test_expired_processing_lease_fences_out_old_provider_candidate(committed_api):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import Session

    api, engine, _ = committed_api
    entered, release, calls = blocked_provider(api, engine)
    payload = {
        "event_id": "lease-recovery",
        "expected_version": api.last["version"],
        "kind": "text",
        "message": "price",
        "target": TARGET,
        "origin": ORIGIN,
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            api.client.post, api.base + "/events", json=payload, headers=api.headers
        )
        try:
            assert entered.wait(5)
            with Session(engine) as db:
                db.execute(
                    update(AIEvent)
                    .where(AIEvent.conversation_id == api.conversation_id)
                    .values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=1))
                )
                db.commit()
            recovered = api.client.post(api.base + "/events", json=payload, headers=api.headers)
            assert recovered.status_code == 200, recovered.text
        finally:
            release.set()
        stale = pending.result(timeout=5)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "EVENT_LEASE_LOST"
    assert len(calls) == 2
    with Session(engine) as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(AIDispatch).where(AIDispatch.conversation_id == api.conversation_id)
                    )
                )
            )
            == 1
        )
        assert db.get(AIEvent, (api.conversation_id, "lease-recovery")).status == "complete"


def test_independent_deadline_workers_settle_once(committed_api):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import Session

    api, engine, _ = committed_api
    api.event("text", "price", target=TARGET, origin=ORIGIN)
    service = api.client.app.state.runtime_service
    clock = datetime.now(timezone.utc) + timedelta(seconds=45)
    service.runtime.conversation.orchestrator.ai.clock = lambda: clock
    with Session(engine) as db:
        db.execute(
            update(AIDispatch)
            .where(AIDispatch.conversation_id == api.conversation_id)
            .values(deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        db.commit()

    def settle():
        with Session(engine) as db:
            return asyncio.run(service.settle_due(db))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: settle(), range(2)))
    assert sum(results) == 1
    assert api.client.get(api.base, headers=api.headers).json()["status"] == "paused"


def test_runtime_quota_blocks_new_provider_work_but_allows_stop_and_read_recovery(api, monkeypatch):
    observed = api.read_product()
    api.confirm(observed)
    api.cart_receipt(api.claim(), 0)
    api.event()
    mutation = api.claim()
    api.receipt(
        mutation,
        {"outcome": "unknown", "target": TARGET, "error_code": "LOST", "observed_at": now()},
    )
    monkeypatch.setattr(get_settings(), "MAX_AI_CALLS_PER_USER_PER_DAY", 0)
    api.event("resume", target=TARGET, origin=ORIGIN)
    api.event()
    assert api.claim()["work"]["tool"]["name"] == "read_cart"
    monkeypatch.setattr(get_settings(), "AI_BROWSER_ORIGINS", ())
    api.event("cancel")
    assert api.last["status"] == "cancelled"
    monkeypatch.setattr(get_settings(), "AI_BROWSER_ORIGINS", (ORIGIN,))
    api.post(
        "/events",
        {
            "event_id": "quota-blocked",
            "expected_version": api.last["version"],
            "kind": "text",
            "message": "hello",
        },
        expected=429,
    )


def test_cors_preflight_only_allows_explicit_local_ui_origin(monkeypatch):
    monkeypatch.setattr(get_settings(), "CORS_ALLOWED_ORIGINS", ("http://localhost:5173",))
    app = create_app()
    with TestClient(app) as client:
        allowed = client.options(
            "/v1/sessions",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
        blocked = client.options(
            "/v1/sessions",
            headers={"Origin": "https://untrusted.test", "Access-Control-Request-Method": "POST"},
        )
        assert blocked.status_code == 400
        assert "Access-Control-Allow-Origin" not in blocked.headers


def test_new_turn_during_synthesis_discards_stale_audio(committed_api):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from shopping_ai.voice import AudioClip, Transcript, VoiceConversation

    api, engine, _ = committed_api
    entered, release = Event(), Event()

    class Speech:
        async def transcribe(self, clip, *, language):
            return Transcript(text="hello")

        async def synthesize(self, text, *, language):
            assert engine.pool.checkedout() == 0
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
            return AudioClip(format="wav", data=b"RIFF0000WAVEobsolete-speech")

    runtime = api.client.app.state.runtime_service.runtime
    voice = Speech()
    runtime.voice = VoiceConversation(runtime.conversation, voice, voice)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            api.client.post,
            api.base + "/speech",
            headers=api.headers,
            json={"expected_version": api.last["version"]},
        )
        try:
            assert entered.wait(5)
            api.event("cancel")
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SPEECH_SUPERSEDED"
    assert "obsolete-speech" not in response.text
