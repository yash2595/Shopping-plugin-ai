import decimal
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import Field
from app.schemas.common import BaseSchema


class SnapshotCreate(BaseSchema):
    retailer: str = Field(..., min_length=1, max_length=100)
    retailer_product_id: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=500)
    url: Optional[str] = None
    price: decimal.Decimal = Field(..., decimal_places=2, gt=0)
    availability: str = Field(..., min_length=1, max_length=50)
    document_id: Optional[str] = Field(None, max_length=255)
    missing_fields: List[str] = Field(default_factory=list)


class PriceSummary(BaseSchema):
    verdict: str  # 'insufficient_data' if < 3 observations or span < MIN_HISTORY_SPAN_DAYS, else 'good_deal', 'fair_price', 'expensive'
    reason: Optional[str] = None  # 'not_enough_observations' | 'history_too_short' | None
    tracking_since: Optional[datetime] = None
    lowest_price: Optional[decimal.Decimal] = None
    highest_price: Optional[decimal.Decimal] = None
    average_price: Optional[decimal.Decimal] = None
    observation_count: int = 0


class SnapshotResponse(BaseSchema):
    snapshot_id: uuid.UUID
    listing_id: uuid.UUID
    price_recorded: bool
    price_summary: PriceSummary


class ObservationCreate(BaseSchema):
    price: decimal.Decimal = Field(..., decimal_places=2, gt=0)
    in_stock: bool
    captured_at: Optional[datetime] = None


class TriggeredAlertRead(BaseSchema):
    alert_id: uuid.UUID
    user_id: uuid.UUID
    target_price: decimal.Decimal
    triggered_price: decimal.Decimal


class ObservationResponse(BaseSchema):
    recorded: bool
    status: str
    triggered_alerts: List[TriggeredAlertRead] = []


class HistoryObservation(BaseSchema):
    price: decimal.Decimal
    in_stock: bool
    captured_at: datetime


class PriceHistoryResponse(BaseSchema):
    listing_id: uuid.UUID
    days: int
    observations: List[HistoryObservation]
    count: int
    min: Optional[decimal.Decimal] = None
    max: Optional[decimal.Decimal] = None
    average: Optional[decimal.Decimal] = None
    first_seen: Optional[datetime] = None
    verdict: str
    reason: Optional[str] = None
    tracking_since: Optional[datetime] = None
