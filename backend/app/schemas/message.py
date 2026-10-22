import uuid
from datetime import datetime
from typing import Optional
from pydantic import Field
from app.schemas.alert import PriceAlertRead
from app.schemas.common import BaseSchema


class MessageCreate(BaseSchema):
    request_id: str = Field(..., min_length=1, max_length=128)
    snapshot_id: Optional[uuid.UUID] = None
    text: str = Field(..., min_length=1)


class MessageRead(BaseSchema):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    kind: Optional[str] = "answer"
    alert: Optional[PriceAlertRead] = None
    request_id: Optional[str] = None
    created_at: datetime
