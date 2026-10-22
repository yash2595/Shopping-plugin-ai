import decimal
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import Field

from app.schemas.common import BaseSchema


class RejectedProductRead(BaseSchema):
    id: uuid.UUID
    listing_id: uuid.UUID
    reason: Optional[str] = None
    created_at: datetime


class ShoppingContextRead(BaseSchema):
    id: uuid.UUID
    session_id: uuid.UUID
    budget_max: Optional[decimal.Decimal] = None
    currency: str = "INR"
    preferences: Dict[str, Any] = Field(default_factory=dict)
    version: int
    created_at: datetime
    updated_at: datetime
    rejections: List[RejectedProductRead] = Field(default_factory=list)


class ShoppingContextUpdate(BaseSchema):
    budget_max: Optional[decimal.Decimal] = None
    preferences: Optional[Dict[str, Optional[Any]]] = None
    version: int


class RejectionCreate(BaseSchema):
    listing_id: uuid.UUID
    reason: Optional[str] = Field(None, max_length=200)
