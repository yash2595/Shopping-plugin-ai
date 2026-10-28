import uuid
from typing import Optional

from app.models.session import Message
from app.models.session import Session as ChatSession
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload


class SessionRepository:
    def claim_request(
        self,
        db: Session,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str,
        content: str,
        payload_hash: str,
    ) -> Optional[Message]:
        """Unique insert is the admission fence; never hold it across a provider call."""
        stmt = (
            insert(Message)
            .values(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id=user_id,
                request_id=request_id,
                content=content,
                role="user",
                kind="user",
                payload={"request_hash": payload_hash, "status": "processing"},
            )
            .on_conflict_do_nothing(constraint="uq_messages_user_request_id")
            .returning(Message)
        )
        return db.scalars(stmt).first()

    def get_request_reply(
        self,
        db: Session,
        *,
        request: Message,
    ) -> Optional[Message]:
        """An exact saved association, never a timestamp-based guess."""
        payload = request.payload or {}
        try:
            reply_id = uuid.UUID(payload["reply_id"])
        except (KeyError, TypeError, ValueError):
            return None
        return db.scalars(
            select(Message).where(
                Message.id == reply_id,
                Message.role == "assistant",
                Message.user_id == request.user_id,
                Message.session_id == request.session_id,
            )
        ).first()

    def finish_request(self, db: Session, *, request: Message, reply: Message) -> None:
        request.payload = {
            "request_hash": request.payload["request_hash"],
            "status": "complete",
            "reply_id": str(reply.id),
        }
        db.execute(
            update(ChatSession)
            .where(
                ChatSession.id == request.session_id,
                ChatSession.user_id == request.user_id,
            )
            .values(updated_at=reply.created_at)
        )
        db.flush()

    def create_session(
        self,
        db: Session,
        user_id: uuid.UUID,
        title: Optional[str] = None,
    ) -> ChatSession:
        session = ChatSession(
            user_id=user_id,
            title=title,
        )
        db.add(session)
        db.flush()
        return session

    def get_session_by_id(
        self,
        db: Session,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[ChatSession]:
        stmt = (
            select(ChatSession)
            .options(selectinload(ChatSession.messages))
            .where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        return db.scalars(stmt).first()

    def create_message(
        self,
        db: Session,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        content: str,
        request_id: Optional[str] = None,
        kind: Optional[str] = "answer",
        payload: Optional[dict] = None,
    ) -> Message:
        message = Message(
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
            request_id=request_id,
            kind=kind,
            payload=payload,
        )
        db.add(message)
        db.flush()
        return message

    def get_recent_messages(
        self,
        db: Session,
        session_id: uuid.UUID,
        limit: int = 10,
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        msgs = list(db.scalars(stmt).all())
        msgs.reverse()
        return msgs

    def get_message_by_user_and_request_id(
        self,
        db: Session,
        user_id: uuid.UUID,
        request_id: str,
    ) -> Optional[Message]:
        stmt = select(Message).where(
            Message.user_id == user_id,
            Message.request_id == request_id,
        )
        return db.scalars(stmt).first()
