import uuid
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.ai.contracts import AIProvider
from app.db import get_db
from app.deps import (
    get_ai_provider,
    get_chat_service,
    get_context_service,
    get_current_user,
    get_session_service,
)
from app.models.user import User
from app.schemas.context import (
    RejectedProductRead,
    RejectionCreate,
    ShoppingContextRead,
    ShoppingContextUpdate,
)
from app.schemas.message import MessageCreate, MessageRead
from app.schemas.session import SessionCreate, SessionDetailRead, SessionRead
from app.services.chat_service import ChatService
from app.services.context_service import ContextService
from app.services.session_service import SessionService

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(
    data: SessionCreate = SessionCreate(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    session_service: SessionService = Depends(get_session_service),
):
    """Create a new chat session for the current user."""
    session = session_service.create_session(
        db,
        user=current_user,
        title=data.title,
        carry_context_from=data.carry_context_from,
    )
    return session


@router.get("/{id}", response_model=SessionDetailRead)
def get_session(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    session_service: SessionService = Depends(get_session_service),
):
    """
    Retrieve session by ID.
    Users can only see their own sessions; another user's session ID returns 404 (not 403).
    """
    session = session_service.get_session(db, user=current_user, session_id=id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session


@router.post("/{id}/messages", response_model=MessageRead, status_code=status.HTTP_200_OK)
def send_message(
    id: uuid.UUID,
    data: MessageCreate,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    chat_service: ChatService = Depends(get_chat_service),
    ai_provider: AIProvider = Depends(get_ai_provider),
):
    """
    Post a message in a session.
    Idempotent by request_id: replaying with the same request_id returns the saved reply
    with header Idempotent-Replay: true.
    """
    msg, is_replay, err = chat_service.process_message(
        db=db,
        user=current_user,
        session_id=id,
        data=data,
        ai_provider=ai_provider,
    )

    if err == "session_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if err == "snapshot_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Snapshot not found",
        )
    if err == "rate_limited":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": "Daily AI limit exceeded",
            },
        )
    if not msg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to process message",
        )

    if is_replay:
        response.headers["Idempotent-Replay"] = "true"

    return msg


@router.get("/{id}/context", response_model=ShoppingContextRead, status_code=status.HTTP_200_OK)
def get_shopping_context(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    context_service: ContextService = Depends(get_context_service),
):
    """Retrieve shopping context for a session."""
    return context_service.get_context(db=db, session_id=id, user_id=current_user.id)


@router.patch("/{id}/context", response_model=ShoppingContextRead, status_code=status.HTTP_200_OK)
def update_shopping_context(
    id: uuid.UUID,
    data: ShoppingContextUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    context_service: ContextService = Depends(get_context_service),
):
    """
    Update shopping context with optimistic locking on version.
    Preferences merge, null values delete keys.
    """
    return context_service.update_context(
        db=db,
        session_id=id,
        user_id=current_user.id,
        data=data,
    )


@router.post("/{id}/context/rejections", response_model=RejectedProductRead, status_code=status.HTTP_200_OK)
def add_product_rejection(
    id: uuid.UUID,
    data: RejectionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    context_service: ContextService = Depends(get_context_service),
):
    """
    Add product rejection to session context.
    User must have captured a snapshot of the listing. Idempotent 200 on duplicate.
    """
    return context_service.add_rejection(
        db=db,
        session_id=id,
        user_id=current_user.id,
        data=data,
    )


@router.delete("/{id}/context/rejections/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_product_rejection(
    id: uuid.UUID,
    listing_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    context_service: ContextService = Depends(get_context_service),
):
    """Remove a product rejection from session context."""
    context_service.remove_rejection(
        db=db,
        session_id=id,
        user_id=current_user.id,
        listing_id=listing_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
