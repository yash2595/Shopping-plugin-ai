import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import Field
from app.schemas.common import BaseSchema
from app.schemas.message import MessageRead


class SessionCreate(BaseSchema):
    title: Optional[str] = Field(None, max_length=255)
    carry_context_from: Optional[uuid.UUID] = None


class SessionRead(BaseSchema):
    id: uuid.UUID
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SessionDetailRead(SessionRead):
    messages: List[MessageRead] = []
