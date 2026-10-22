import decimal
import uuid
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.context import RejectedProduct, ShoppingContext
from app.repositories.context_repo import ContextRepository
from app.repositories.session_repo import SessionRepository
from app.repositories.snapshot_repo import SnapshotRepository
from app.schemas.context import RejectionCreate, ShoppingContextUpdate

settings = get_settings()


class ContextService:
    def __init__(
        self,
        context_repo: Optional[ContextRepository] = None,
        session_repo: Optional[SessionRepository] = None,
        snapshot_repo: Optional[SnapshotRepository] = None,
    ):
        self.context_repo = context_repo or ContextRepository()
        self.session_repo = session_repo or SessionRepository()
        self.snapshot_repo = snapshot_repo or SnapshotRepository()

    def get_context(
        self,
        db: Session,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ShoppingContext:
        session = self.session_repo.get_session_by_id(db, session_id=session_id, user_id=user_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "not_found", "message": "Session not found"},
            )
        ctx = self.context_repo.get_context_by_session_id(db, session_id=session_id, user_id=user_id)
        if not ctx:
            ctx = self.context_repo.create_context(db, user_id=user_id, session_id=session_id)
            db.commit()
            db.refresh(ctx)
        return ctx

    def update_context(
        self,
        db: Session,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        data: ShoppingContextUpdate,
    ) -> ShoppingContext:
        ctx = self.get_context(db, session_id=session_id, user_id=user_id)

        # 1. Validate preferences against whitelist
        new_preferences = dict(ctx.preferences)
        if data.preferences is not None:
            for k, v in data.preferences.items():
                if k not in settings.PREFERENCE_WHITELIST:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail={
                            "code": "INVALID_PREFERENCE_KEY",
                            "message": f"Unknown preference key '{k}'. Whitelist: {sorted(settings.PREFERENCE_WHITELIST)}",
                        },
                    )
                if v is None:
                    # A null value deletes a key
                    new_preferences.pop(k, None)
                else:
                    if isinstance(v, str):
                        if len(v) > settings.MAX_PREFERENCE_STRING_LENGTH:
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={
                                    "code": "INVALID_PREFERENCE_VALUE",
                                    "message": f"Preference string value too long (max {settings.MAX_PREFERENCE_STRING_LENGTH} chars)",
                                },
                            )
                    elif isinstance(v, int):
                        if v < 0 or v > settings.MAX_PREFERENCE_INT_VALUE:
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={
                                    "code": "INVALID_PREFERENCE_VALUE",
                                    "message": f"Preference integer out of bounds (0-{settings.MAX_PREFERENCE_INT_VALUE})",
                                },
                            )
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail={
                                "code": "INVALID_PREFERENCE_VALUE",
                                "message": "Preference value must be a string or integer",
                            },
                        )
                    new_preferences[k] = v

        # 2. Validate budget
        update_budget = False
        new_budget = ctx.budget_max
        if data.budget_max is not None:
            if data.budget_max <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "INVALID_BUDGET",
                        "message": "budget_max must be greater than 0",
                    },
                )
            new_budget = data.budget_max
            update_budget = True

        # 3. Atomic optimistic locking update
        updated = self.context_repo.update_context_atomic(
            db,
            context_id=ctx.id,
            expected_version=data.version,
            budget_max=new_budget,
            preferences=new_preferences if data.preferences is not None else None,
            update_budget=update_budget,
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "VERSION_CONFLICT",
                    "message": "Shopping context version conflict. Please re-fetch and retry.",
                },
            )
        db.commit()
        db.refresh(updated)
        return updated

    def add_rejection(
        self,
        db: Session,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        data: RejectionCreate,
    ) -> RejectedProduct:
        ctx = self.get_context(db, session_id=session_id, user_id=user_id)

        # Verify listing exists
        listing = self.snapshot_repo.get_listing_by_id(db, data.listing_id)
        if not listing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "not_found", "message": "Listing not found"},
            )

        # Caller must have captured at least one snapshot for this listing
        has_seen = self.snapshot_repo.has_user_snapshot_for_listing(
            db, user_id=user_id, listing_id=data.listing_id
        )
        if not has_seen:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "LISTING_NOT_SEEN",
                    "message": "User has not captured a snapshot for this listing",
                },
            )

        rejection, _ = self.context_repo.add_rejection(
            db,
            context_id=ctx.id,
            listing_id=data.listing_id,
            reason=data.reason,
        )
        db.commit()
        db.refresh(rejection)
        return rejection

    def remove_rejection(
        self,
        db: Session,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        listing_id: uuid.UUID,
    ) -> None:
        ctx = self.get_context(db, session_id=session_id, user_id=user_id)
        self.context_repo.remove_rejection(db, context_id=ctx.id, listing_id=listing_id)
        db.commit()

    def carry_context_from_session(
        self,
        db: Session,
        source_session_id: uuid.UUID,
        target_context: ShoppingContext,
        user_id: uuid.UUID,
    ) -> ShoppingContext:
        source_session = self.session_repo.get_session_by_id(
            db, session_id=source_session_id, user_id=user_id
        )
        if not source_session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "not_found", "message": "Source session not found"},
            )
        source_ctx = self.context_repo.get_context_by_session_id(
            db, session_id=source_session_id, user_id=user_id
        )
        if source_ctx:
            target_context.budget_max = source_ctx.budget_max
            target_context.currency = source_ctx.currency
            target_context.preferences = dict(source_ctx.preferences)
            for rej in source_ctx.rejections:
                self.context_repo.add_rejection(
                    db,
                    context_id=target_context.id,
                    listing_id=rej.listing_id,
                    reason=rej.reason,
                )
            db.flush()
        return target_context
