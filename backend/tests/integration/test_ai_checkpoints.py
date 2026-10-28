"""Real PostgreSQL tests of the optional core checkpoint persistence boundary."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytest.importorskip("shopping_ai")

from app.config import get_settings
from app.models.session import AIConversation
from app.models.user import User
from app.repositories.ai_checkpoint_repo import AICheckpointRepository
from app.services.auth_service import AuthService
from app.services.session_service import SessionService
from shopping_ai import Conversation, Orchestrator, Principal, ShoppingAI
from shopping_ai.checkpoint import CheckpointError, dump_checkpoint
from shopping_ai.conversation import ConversationTurn
from shopping_ai.models import ProductSnapshot
from shopping_ai.providers import MockProvider


def initial_state(db):
    auth = AuthService()
    token, _ = auth.register_anonymous_user(db)
    user = auth.authenticate_token(db, token)
    session = SessionService().create_session(db, user)
    principal = Principal(owner_id=str(user.id), session_id=str(session.id))
    conversation = Conversation(Orchestrator(ShoppingAI(MockProvider())))
    return principal, conversation, conversation.start(principal).state


def test_exact_checkpoint_roundtrip_and_version_jump(db_session):
    principal, conversation, state = initial_state(db_session)
    product = ProductSnapshot(
        snapshot_id="synthetic-snapshot",
        product_id="synthetic-product",
        document_id="document-1",
        title="Synthetic phone",
        observed_at=datetime.now(timezone.utc),
        price=Decimal("100.10"),
        currency="INR",
        in_stock=True,
    )
    state = state.model_copy(update={"products": (product,), "selected_id": product.product_id})
    repo = AICheckpointRepository()
    repo.create(db_session, principal, state)
    db_session.commit()
    row = db_session.get(AIConversation, state.conversation_id)
    assert row.checkpoint.encode("utf-8") == dump_checkpoint(state, principal)
    assert repo.load(db_session, principal, state.conversation_id) == state
    assert repo.load(db_session, principal, state.conversation_id).products[0].price == Decimal(
        "100.10"
    )
    next_state = asyncio.run(
        conversation.respond(
            state,
            principal,
            ConversationTurn(request_id="turn-1", message="hello"),
            expected_version=state.version,
        )
    ).state
    assert next_state.version > state.version
    next_state = next_state.model_copy(update={"version": next_state.version + 2})
    assert repo.compare_and_swap(db_session, principal, next_state, expected_version=state.version)
    db_session.commit()
    assert repo.load(db_session, principal, state.conversation_id) == next_state
    assert not repo.compare_and_swap(
        db_session, principal, next_state, expected_version=state.version
    )


def test_foreign_owner_and_session_cannot_load_or_update(db_session):
    principal, _, state = initial_state(db_session)
    other, _, _ = initial_state(db_session)
    repo = AICheckpointRepository()
    repo.create(db_session, principal, state)
    db_session.commit()
    for foreign in [
        other,
        Principal(owner_id=principal.owner_id, session_id=other.session_id),
        Principal(owner_id=other.owner_id, session_id=principal.session_id),
    ]:
        assert repo.load(db_session, foreign, state.conversation_id) is None
        candidate = state.model_copy(update={"principal": foreign, "version": state.version + 1})
        assert not repo.compare_and_swap(
            db_session, foreign, candidate, expected_version=state.version
        )
    with pytest.raises(CheckpointError, match="ACCESS_DENIED"):
        repo.compare_and_swap(db_session, other, state, expected_version=state.version)
    assert repo.load(db_session, principal, state.conversation_id) == state


def test_database_rejects_mismatched_owner_session_even_when_repository_is_bypassed(db_session):
    principal, _, state = initial_state(db_session)
    other, _, _ = initial_state(db_session)
    import uuid

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            AIConversation(
                conversation_id=state.conversation_id,
                user_id=uuid.UUID(other.owner_id),
                session_id=uuid.UUID(principal.session_id),
                version=0,
                schema_version=1,
                checkpoint=dump_checkpoint(state, principal).decode(),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()


def test_same_version_cannot_replace_different_bytes_and_invalid_versions_fail(db_session):
    principal, _, state = initial_state(db_session)
    repo = AICheckpointRepository()
    repo.create(db_session, principal, state)
    db_session.commit()
    assert repo.compare_and_swap(db_session, principal, state, expected_version=state.version)
    altered = state.model_copy(update={"language": "en"})
    assert not repo.compare_and_swap(db_session, principal, altered, expected_version=state.version)
    for expected in [True, -1, "0", state.version + 1]:
        with pytest.raises(CheckpointError, match="VERSION_MISMATCH"):
            repo.compare_and_swap(db_session, principal, state, expected_version=expected)


def test_checkpoint_metadata_is_verified_on_restore(db_session):
    principal, _, state = initial_state(db_session)
    repo = AICheckpointRepository()
    repo.create(db_session, principal, state)
    db_session.commit()
    db_session.execute(
        update(AIConversation)
        .where(
            AIConversation.conversation_id == state.conversation_id,
        )
        .values(version=state.version + 1)
    )
    with pytest.raises(CheckpointError, match="VERSION_MISMATCH"):
        repo.load(db_session, principal, state.conversation_id)


def test_checkpoint_cas_rolls_back_with_callers_transaction(db_session):
    principal, _, state = initial_state(db_session)
    repo = AICheckpointRepository()
    repo.create(db_session, principal, state)
    db_session.commit()
    candidate = state.model_copy(update={"version": state.version + 1})
    assert repo.compare_and_swap(db_session, principal, candidate, expected_version=state.version)
    db_session.rollback()
    assert repo.load(db_session, principal, state.conversation_id) == state


def test_two_independent_connections_at_same_version_have_exactly_one_winner():
    engine = create_engine(
        get_settings().TEST_DATABASE_URL, connect_args={"prepare_threshold": None}
    )
    repo = AICheckpointRepository()
    principal = None
    try:
        with Session(engine) as db:
            principal, _, state = initial_state(db)
            repo.create(db, principal, state)
            db.commit()
        barrier = Barrier(2)

        def write(language):
            with Session(engine) as db:
                original = repo.load(db, principal, state.conversation_id)
                assert original == state
                candidate = original.model_copy(
                    update={"version": state.version + 1, "language": language}
                )
                barrier.wait(timeout=5)
                won = repo.compare_and_swap(
                    db, principal, candidate, expected_version=state.version
                )
                db.commit()
                return won, candidate

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, language) for language in ("en", "hi-Latn")]
            results = [future.result(timeout=10) for future in futures]
        assert sum(won for won, _ in results) == 1
        winner = next(candidate for won, candidate in results if won)
        with Session(engine) as db:
            assert repo.load(db, principal, state.conversation_id) == winner
            assert db.scalar(
                select(AIConversation.checkpoint).where(
                    AIConversation.conversation_id == state.conversation_id,
                )
            ).encode() == dump_checkpoint(winner, principal)
    finally:
        if principal:
            import uuid

            with Session(engine) as db:
                db.execute(delete(User).where(User.id == uuid.UUID(principal.owner_id)))
                db.commit()
        engine.dispose()
