"""Owner-scoped core persistence; requires the optional, locally installed AI package.

Use from a trusted application service after authenticating the principal. Methods
participate in the caller's short transaction and never commit or dispatch work.
Future event/outbox writes must share the same transaction as compare_and_swap.
"""

import uuid
from datetime import datetime, timezone

from app.models.session import AIConversation
from app.models.session import Session as ChatSession
from shopping_ai.checkpoint import (
    SCHEMA_VERSION,
    CheckpointError,
    dump_checkpoint,
    load_checkpoint,
)
from shopping_ai.conversation import ConversationState
from shopping_ai.orchestrator import Principal
from sqlalchemy import select, update
from sqlalchemy.orm import Session


def _ids(principal: Principal) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        owner_id, session_id = uuid.UUID(principal.owner_id), uuid.UUID(principal.session_id)
        if (str(owner_id), str(session_id)) != (principal.owner_id, principal.session_id):
            raise ValueError
        return owner_id, session_id
    except (ValueError, TypeError, AttributeError):
        raise CheckpointError("CHECKPOINT_ACCESS_DENIED") from None


class AICheckpointRepository:
    def create(self, db: Session, principal: Principal, state: ConversationState) -> None:
        payload = dump_checkpoint(state, principal)
        owner_id, session_id = _ids(principal)
        owned = db.scalar(
            select(ChatSession.id).where(
                ChatSession.id == session_id,
                ChatSession.user_id == owner_id,
            )
        )
        if owned is None:
            raise CheckpointError("CHECKPOINT_SESSION_NOT_FOUND")
        db.add(
            AIConversation(
                conversation_id=state.conversation_id,
                user_id=owner_id,
                session_id=session_id,
                version=state.version,
                schema_version=SCHEMA_VERSION,
                checkpoint=payload.decode("utf-8"),
            )
        )
        db.flush()

    def load(
        self,
        db: Session,
        principal: Principal,
        conversation_id: str,
    ) -> ConversationState | None:
        owner_id, session_id = _ids(principal)
        row = db.execute(
            select(
                AIConversation.version,
                AIConversation.schema_version,
                AIConversation.checkpoint,
            ).where(
                AIConversation.conversation_id == conversation_id,
                AIConversation.user_id == owner_id,
                AIConversation.session_id == session_id,
            )
        ).first()
        if row is None:
            return None
        if row.schema_version != SCHEMA_VERSION:
            raise CheckpointError("CHECKPOINT_SCHEMA_UNSUPPORTED")
        return load_checkpoint(
            row.checkpoint.encode("utf-8"),
            principal,
            conversation_id=conversation_id,
            expected_version=row.version,
        )

    def compare_and_swap(
        self,
        db: Session,
        principal: Principal,
        state: ConversationState,
        *,
        expected_version: int,
    ) -> bool:
        payload = dump_checkpoint(state, principal)
        if (
            type(expected_version) is not int
            or expected_version < 0
            or state.version < expected_version
        ):
            raise CheckpointError("CHECKPOINT_VERSION_MISMATCH")
        owner_id, session_id = _ids(principal)
        stmt = update(AIConversation).where(
            AIConversation.conversation_id == state.conversation_id,
            AIConversation.user_id == owner_id,
            AIConversation.session_id == session_id,
            AIConversation.version == expected_version,
        )
        # A no-state-change event may persist an event result, but cannot replace
        # different bytes at the same version and thereby bypass the CAS fence.
        if state.version == expected_version:
            stmt = stmt.where(AIConversation.checkpoint == payload.decode("utf-8"))
        result = db.scalar(
            stmt.values(
                version=state.version,
                schema_version=SCHEMA_VERSION,
                checkpoint=payload.decode("utf-8"),
                updated_at=datetime.now(timezone.utc),
            ).returning(AIConversation.conversation_id)
        )
        return result is not None
