import decimal
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.models.context import RejectedProduct, ShoppingContext
from app.models.product import Listing


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContextRepository:
    def get_context_by_session_id(
        self,
        db: Session,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[ShoppingContext]:
        stmt = (
            select(ShoppingContext)
            .options(
                selectinload(ShoppingContext.rejections).selectinload(RejectedProduct.listing)
            )
            .where(
                ShoppingContext.session_id == session_id,
                ShoppingContext.user_id == user_id,
            )
        )
        return db.scalars(stmt).first()

    def get_context_by_id(
        self,
        db: Session,
        context_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[ShoppingContext]:
        stmt = (
            select(ShoppingContext)
            .options(
                selectinload(ShoppingContext.rejections).selectinload(RejectedProduct.listing)
            )
            .where(
                ShoppingContext.id == context_id,
                ShoppingContext.user_id == user_id,
            )
        )
        return db.scalars(stmt).first()

    def create_context(
        self,
        db: Session,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        budget_max: Optional[decimal.Decimal] = None,
        currency: str = "INR",
        preferences: Optional[Dict[str, Any]] = None,
    ) -> ShoppingContext:
        ctx = ShoppingContext(
            user_id=user_id,
            session_id=session_id,
            budget_max=budget_max,
            currency=currency,
            preferences=preferences or {},
            version=1,
        )
        db.add(ctx)
        db.flush()
        return ctx

    def update_context_atomic(
        self,
        db: Session,
        context_id: uuid.UUID,
        expected_version: int,
        budget_max: Optional[decimal.Decimal] = None,
        preferences: Optional[Dict[str, Any]] = None,
        update_budget: bool = False,
    ) -> Optional[ShoppingContext]:
        now = utc_now()
        values: Dict[str, Any] = {
            "version": ShoppingContext.version + 1,
            "updated_at": now,
        }
        if update_budget:
            values["budget_max"] = budget_max
        if preferences is not None:
            values["preferences"] = preferences

        stmt = (
            update(ShoppingContext)
            .where(
                ShoppingContext.id == context_id,
                ShoppingContext.version == expected_version,
            )
            .values(**values)
            .returning(ShoppingContext)
        )
        return db.scalars(stmt).first()

    def add_rejection(
        self,
        db: Session,
        context_id: uuid.UUID,
        listing_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> Tuple[RejectedProduct, bool]:
        """
        Idempotent product rejection. Returns (RejectedProduct, created).
        """
        existing = db.scalars(
            select(RejectedProduct).where(
                RejectedProduct.context_id == context_id,
                RejectedProduct.listing_id == listing_id,
            )
        ).first()

        if existing:
            if reason is not None and existing.reason != reason:
                existing.reason = reason
                db.flush()
            return existing, False

        rejection = RejectedProduct(
            context_id=context_id,
            listing_id=listing_id,
            reason=reason,
        )
        db.add(rejection)
        db.flush()
        return rejection, True

    def remove_rejection(
        self,
        db: Session,
        context_id: uuid.UUID,
        listing_id: uuid.UUID,
    ) -> bool:
        stmt = delete(RejectedProduct).where(
            RejectedProduct.context_id == context_id,
            RejectedProduct.listing_id == listing_id,
        )
        res = db.execute(stmt)
        db.flush()
        return (res.rowcount or 0) > 0

    def get_rejected_listing_titles(
        self,
        db: Session,
        context_id: uuid.UUID,
    ) -> List[str]:
        stmt = (
            select(Listing.title)
            .join(RejectedProduct, RejectedProduct.listing_id == Listing.id)
            .where(RejectedProduct.context_id == context_id)
        )
        return list(db.scalars(stmt).all())
