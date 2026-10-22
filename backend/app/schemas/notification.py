import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from app.schemas.common import BaseSchema


class NotificationRead(BaseSchema):
    id: uuid.UUID
    alert_id: uuid.UUID
    user_id: uuid.UUID
    arm_count: int
    channel: str
    status: str
    payload: Dict[str, Any]
    created_at: datetime
    delivered_at: Optional[datetime] = None
