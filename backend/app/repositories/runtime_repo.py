"""Short PostgreSQL transactions for runtime admission and a fenced browser outbox."""

import uuid
from datetime import datetime, timedelta, timezone

from app.models.session import AIConversation, AIDispatch, AIEvent
from app.models.session import Session as ChatSession
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert


def utcnow():
    return datetime.now(timezone.utc)


class RuntimeRepository:
    def session(self, db, owner, session_id, *, lock=False, skip_locked=False):
        stmt = select(ChatSession).where(ChatSession.user_id == owner, ChatSession.id == session_id)
        if lock:
            stmt = stmt.with_for_update(skip_locked=skip_locked)
        return db.scalar(stmt)

    def conversation(self, db, owner, conversation_id):
        return db.scalar(
            select(AIConversation)
            .where(
                AIConversation.user_id == owner,
                AIConversation.conversation_id == conversation_id,
            )
            .execution_options(populate_existing=True)
        )

    def for_session(self, db, owner, session_id):
        return db.scalar(
            select(AIConversation).where(
                AIConversation.user_id == owner,
                AIConversation.session_id == session_id,
            )
        )

    def event(self, db, owner, conversation_id, event_id, *, lock=False):
        stmt = (
            select(AIEvent)
            .where(
                AIEvent.user_id == owner,
                AIEvent.conversation_id == conversation_id,
                AIEvent.event_id == event_id,
            )
            .execution_options(populate_existing=True)
        )
        return db.scalar(stmt.with_for_update() if lock else stmt)

    def admit(
        self, db, owner, session_id, conversation_id, event_id, payload_hash, version, seconds
    ):
        token = uuid.uuid4()
        row = db.scalar(
            insert(AIEvent)
            .values(
                conversation_id=conversation_id,
                event_id=event_id,
                user_id=owner,
                session_id=session_id,
                payload_hash=payload_hash,
                expected_version=version,
                status="processing",
                lease_token=token,
                lease_until=utcnow() + timedelta(seconds=seconds),
            )
            .on_conflict_do_nothing()
            .returning(AIEvent.event_id)
        )
        return token if row is not None else None

    def reclaim(self, db, event, seconds):
        token = uuid.uuid4()
        value = db.scalar(
            update(AIEvent)
            .where(
                AIEvent.conversation_id == event.conversation_id,
                AIEvent.event_id == event.event_id,
                AIEvent.lease_token == event.lease_token,
                AIEvent.status == "processing",
                AIEvent.lease_until <= utcnow(),
            )
            .values(lease_token=token, lease_until=utcnow() + timedelta(seconds=seconds))
            .returning(AIEvent.event_id)
        )
        return token if value is not None else None

    def fail(self, db, owner, conversation_id, event_id, token, result):
        db.execute(
            update(AIEvent)
            .where(
                AIEvent.user_id == owner,
                AIEvent.conversation_id == conversation_id,
                AIEvent.event_id == event_id,
                AIEvent.lease_token == token,
                AIEvent.status == "processing",
            )
            .values(status="failed", result=result)
        )

    def public_reply(self, db, owner, conversation_id, reply):
        db.execute(
            update(AIConversation)
            .where(
                AIConversation.user_id == owner,
                AIConversation.conversation_id == conversation_id,
            )
            .values(public_reply=reply)
        )

    def dispatch(self, db, owner, conversation_id, dispatch_id, *, lock=False):
        stmt = (
            select(AIDispatch)
            .where(
                AIDispatch.user_id == owner,
                AIDispatch.conversation_id == conversation_id,
                AIDispatch.dispatch_id == dispatch_id,
            )
            .execution_options(populate_existing=True)
        )
        return db.scalar(stmt.with_for_update() if lock else stmt)

    def work(self, db, owner, conversation_id):
        return list(
            db.scalars(
                select(AIDispatch)
                .where(
                    AIDispatch.user_id == owner,
                    AIDispatch.conversation_id == conversation_id,
                    AIDispatch.status.in_(["pending", "claimed"]),
                    AIDispatch.deadline_at > utcnow(),
                )
                .order_by(AIDispatch.dispatch_id)
            )
        )

    def active_claim(self, db, owner, session_id, dispatch_id):
        return db.scalar(
            select(AIDispatch.dispatch_id)
            .where(
                AIDispatch.user_id == owner,
                AIDispatch.session_id == session_id,
                AIDispatch.dispatch_id != dispatch_id,
                AIDispatch.status == "claimed",
            )
            .limit(1)
        )

    def reconcile_outbox(self, db, principal, state, origin, proposals, constraints):
        """Called after checkpoint CAS, in the same transaction as the saved reply."""
        owner, session_id = uuid.UUID(principal.owner_id), uuid.UUID(principal.session_id)
        live = set()
        if state.active_run and state.active_run.pending and state.active_run.status != "cancelled":
            live.add(state.active_run.pending.action_id)
        if state.pending_navigation:
            live.add(state.pending_navigation.action_id)
        if state.active_market:
            completed = {r.task_id for r in state.active_market.receipts}
            live.update(
                task.task_id for task in state.active_market.tasks if task.task_id not in completed
            )
        stmt = update(AIDispatch).where(
            AIDispatch.user_id == owner,
            AIDispatch.conversation_id == state.conversation_id,
            AIDispatch.status.in_(["pending", "claimed"]),
        )
        if live:
            stmt = stmt.where(AIDispatch.dispatch_id.not_in(live))
        db.execute(stmt.values(status="suppressed"))
        for dispatch_id, kind, payload, deadline in proposals:
            db.execute(
                insert(AIDispatch)
                .values(
                    dispatch_id=dispatch_id,
                    user_id=owner,
                    session_id=session_id,
                    conversation_id=state.conversation_id,
                    kind=kind,
                    payload={
                        "work": payload,
                        "constraints": constraints if kind == "browser" else None,
                    },
                    origin=origin or "",
                    version=state.version,
                    deadline_at=deadline,
                    status="pending",
                )
                .on_conflict_do_nothing(index_elements=[AIDispatch.dispatch_id])
            )
        db.execute(
            update(ChatSession)
            .where(
                ChatSession.user_id == owner,
                ChatSession.id == session_id,
            )
            .values(updated_at=utcnow())
        )

    def due(self, db, limit=20):
        return list(
            db.scalars(
                select(AIDispatch)
                .where(
                    AIDispatch.status.in_(["pending", "claimed"]),
                    AIDispatch.deadline_at <= utcnow(),
                )
                .order_by(AIDispatch.deadline_at)
                .limit(limit)
            )
        )

    def delete_conversation(self, db, owner, conversation_id):
        db.execute(
            delete(AIConversation).where(
                AIConversation.user_id == owner,
                AIConversation.conversation_id == conversation_id,
            )
        )
