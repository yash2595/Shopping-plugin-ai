import decimal
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.product import Listing, PriceObservation, ProductSnapshot


class SnapshotRepository:
    def get_listing_by_id(
        self,
        db: Session,
        listing_id: uuid.UUID,
    ) -> Optional[Listing]:
        stmt = select(Listing).where(Listing.id == listing_id)
        return db.scalars(stmt).first()

    def get_listing_by_identity(
        self,
        db: Session,
        retailer: str,
        retailer_product_id: str,
    ) -> Optional[Listing]:
        stmt = select(Listing).where(
            Listing.retailer == retailer,
            Listing.retailer_product_id == retailer_product_id,
        )
        return db.scalars(stmt).first()

    def create_listing(
        self,
        db: Session,
        retailer: str,
        retailer_product_id: str,
        title: str,
        url: Optional[str] = None,
    ) -> Listing:
        listing = Listing(
            retailer=retailer,
            retailer_product_id=retailer_product_id,
            title=title,
            url=url,
        )
        db.add(listing)
        db.flush()
        return listing

    def update_listing(
        self,
        db: Session,
        listing: Listing,
        title: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Listing:
        if title:
            listing.title = title
        if url:
            listing.url = url
        db.flush()
        return listing

    def create_snapshot(
        self,
        db: Session,
        user_id: uuid.UUID,
        listing_id: uuid.UUID,
        captured_price: decimal.Decimal,
        availability: str,
        document_id: Optional[str] = None,
        missing_fields: Optional[List[str]] = None,
    ) -> ProductSnapshot:
        snapshot = ProductSnapshot(
            user_id=user_id,
            listing_id=listing_id,
            captured_price=captured_price,
            availability=availability,
            document_id=document_id,
            missing_fields=missing_fields or [],
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    def get_snapshot_by_id(
        self,
        db: Session,
        snapshot_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[ProductSnapshot]:
        stmt = (
            select(ProductSnapshot)
            .options(selectinload(ProductSnapshot.listing))
            .where(
                ProductSnapshot.id == snapshot_id,
                ProductSnapshot.user_id == user_id,
            )
        )
        return db.scalars(stmt).first()

    def has_identical_price_observation_since(
        self,
        db: Session,
        listing_id: uuid.UUID,
        price: decimal.Decimal,
        since: datetime,
    ) -> bool:
        stmt = select(PriceObservation.id).where(
            PriceObservation.listing_id == listing_id,
            PriceObservation.price == price,
            PriceObservation.recorded_at >= since,
        )
        return db.scalars(stmt).first() is not None

    def get_latest_accepted_observation(
        self,
        db: Session,
        listing_id: uuid.UUID,
    ) -> Optional[PriceObservation]:
        stmt = (
            select(PriceObservation)
            .where(
                PriceObservation.listing_id == listing_id,
                PriceObservation.status == "accepted",
            )
            .order_by(PriceObservation.captured_at.desc())
        )
        return db.scalars(stmt).first()

    def get_all_accepted_observations(
        self,
        db: Session,
        listing_id: uuid.UUID,
    ) -> List[PriceObservation]:
        stmt = (
            select(PriceObservation)
            .where(
                PriceObservation.listing_id == listing_id,
                PriceObservation.status == "accepted",
            )
            .order_by(PriceObservation.captured_at.asc())
        )
        return list(db.scalars(stmt).all())

    def get_history_observations(
        self,
        db: Session,
        listing_id: uuid.UUID,
        since: datetime,
    ) -> List[PriceObservation]:
        stmt = (
            select(PriceObservation)
            .where(
                PriceObservation.listing_id == listing_id,
                PriceObservation.status == "accepted",
                PriceObservation.captured_at >= since,
            )
            .order_by(PriceObservation.captured_at.asc())
        )
        return list(db.scalars(stmt).all())

    def get_first_accepted_observation(
        self,
        db: Session,
        listing_id: uuid.UUID,
    ) -> Optional[PriceObservation]:
        stmt = (
            select(PriceObservation)
            .where(
                PriceObservation.listing_id == listing_id,
                PriceObservation.status == "accepted",
            )
            .order_by(PriceObservation.captured_at.asc())
        )
        return db.scalars(stmt).first()

    def has_user_snapshot_for_listing(
        self,
        db: Session,
        user_id: uuid.UUID,
        listing_id: uuid.UUID,
    ) -> bool:
        stmt = select(ProductSnapshot.id).where(
            ProductSnapshot.user_id == user_id,
            ProductSnapshot.listing_id == listing_id,
        )
        return db.scalars(stmt).first() is not None

    def create_price_observation(
        self,
        db: Session,
        listing_id: uuid.UUID,
        price: decimal.Decimal,
        in_stock: bool,
        status: str = "accepted",
        source: str = "snapshot",
        user_id: Optional[uuid.UUID] = None,
        captured_at: Optional[datetime] = None,
        recorded_at: Optional[datetime] = None,
    ) -> PriceObservation:
        now = datetime.now(timezone.utc)
        obs = PriceObservation(
            listing_id=listing_id,
            price=price,
            in_stock=in_stock,
            status=status,
            source=source,
            user_id=user_id,
            captured_at=captured_at or now,
            recorded_at=recorded_at or now,
        )
        db.add(obs)
        db.flush()
        return obs
