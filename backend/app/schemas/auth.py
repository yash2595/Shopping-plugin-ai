from datetime import datetime
from app.schemas.common import BaseSchema


class RegisterResponse(BaseSchema):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
