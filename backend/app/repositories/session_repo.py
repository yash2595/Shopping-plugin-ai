import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.session import Message, Session as ChatSession


class SessionRepository:
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

    def get_assistant_reply_following(
        self,
        db: Session,
        user_message: Message,
    ) -> Optional[Message]:
        stmt = (
            select(Message)
            .where(
                Message.session_id == user_message.session_id,
                Message.role == "assistant",
                Message.created_at >= user_message.created_at,
            )
            .order_by(Message.created_at.asc())
        )
        return db.scalars(stmt).first()
