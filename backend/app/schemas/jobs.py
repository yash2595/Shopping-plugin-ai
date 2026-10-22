from app.schemas.common import BaseSchema


class JobResult(BaseSchema):
    expired_tokens: int
    deleted_snapshots: int
    deleted_messages: int
    deleted_notifications: int
    deleted_sessions: int = 0
    deleted_review_summaries: int = 0
