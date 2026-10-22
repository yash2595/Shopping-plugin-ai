import decimal
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import Field

from app.schemas.common import BaseSchema


class ReviewItemInput(BaseSchema):
    rating: int = Field(..., ge=1, le=5)
    title: Optional[str] = Field(None, max_length=500)
    text: str
    reviewed_at: Optional[datetime] = None


class ReviewSummaryPostRequest(BaseSchema):
    reviews: List[ReviewItemInput]
    displayed_total_reviews: Optional[int] = None
    displayed_average_rating: Optional[decimal.Decimal] = None


class ReviewSummaryResponse(BaseSchema):
    listing_id: uuid.UUID
    overall: str
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)
    themes: List[str] = Field(default_factory=list)
    caveat: str
    sample_size: int
    average_rating_in_sample: decimal.Decimal
    cached: bool = False
    kind: str = "summary"
    created_at: datetime
