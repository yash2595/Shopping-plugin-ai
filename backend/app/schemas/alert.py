import decimal
import uuid
from datetime import datetime
from typing import Optional
from pydantic import Field
from app.schemas.common import BaseSchema


class AlertCreate(BaseSchema):
    listing_id: uuid.UUID
    target_price: decimal.Decimal = Field(..., decimal_places=2, gt=0)


class AlertUpdate(BaseSchema):
    version: int = Field(..., ge=1)
    target_price: Optional[decimal.Decimal] = Field(None, decimal_places=2, gt=0)
    status: Optional[str] = Field(None, pattern="^(cancelled)$")


class AlertRead(BaseSchema):
    id: uuid.UUID
    user_id: uuid.UUID
    listing_id: uuid.UUID
    target_price: decimal.Decimal
    status: str
    arm_count: int
    triggered_at: Optional[datetime] = None
    triggered_price: Optional[decimal.Decimal] = None
    version: int
    created_at: datetime
    updated_at: datetime


PriceAlertRead = AlertRead
