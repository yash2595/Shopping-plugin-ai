import uuid
from typing import Optional
from sqlalchemy.orm import Session

from app.models.session import Session as ChatSession
from app.models.user import User
from app.repositories.context_repo import ContextRepository
from app.repositories.session_repo import SessionRepository
from app.services.context_service import ContextService


class SessionService:
    def __init__(
        self,
        session_repo: Optional[SessionRepository] = None,
        context_repo: Optional[ContextRepository] = None,
        context_service: Optional[ContextService] = None,
    ):
        self.session_repo = session_repo or SessionRepository()
        self.context_repo = context_repo or ContextRepository()
        self.context_service = context_service or ContextService(
            context_repo=self.context_repo,
            session_repo=self.session_repo,
        )

    def create_session(
        self,
        db: Session,
        user: User,
        title: Optional[str] = None,
        carry_context_from: Optional[uuid.UUID] = None,
    ) -> ChatSession:
        session = self.session_repo.create_session(db, user_id=user.id, title=title)
        # B1: One context row is created together with every session.
        ctx = self.context_repo.create_context(db, user_id=user.id, session_id=session.id)
        if carry_context_from:
            self.context_service.carry_context_from_session(
                db,
                source_session_id=carry_context_from,
                target_context=ctx,
                user_id=user.id,
            )
        db.commit()
        db.refresh(session)
        return session

    def get_session(
        self,
        db: Session,
        user: User,
        session_id: uuid.UUID,
    ) -> Optional[ChatSession]:
        # Strictly filters by user_id. If belonging to another user, returns None (caller raises 404).
        return self.session_repo.get_session_by_id(db, session_id=session_id, user_id=user.id)
