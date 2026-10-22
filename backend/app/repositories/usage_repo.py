import datetime
import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.usage import UsageCounter


class UsageRepository:
    def increment_and_get_count(
        self,
        db: Session,
        user_id: uuid.UUID,
        day: datetime.date,
        key: str,
    ) -> int:
        """
        Atomically increment counter for (user_id, day, key) and return the updated count.
        """
        stmt = (
            insert(UsageCounter)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                day=day,
                key=key,
                count=1,
            )
            .on_conflict_do_update(
                constraint="uq_usage_counters_user_day_key",
                set_={"count": UsageCounter.count + 1},
            )
            .returning(UsageCounter.count)
        )
        return db.scalar(stmt) or 1

    def get_count(
        self,
        db: Session,
        user_id: uuid.UUID,
        day: datetime.date,
        key: str,
    ) -> int:
        stmt = select(UsageCounter.count).where(
            UsageCounter.user_id == user_id,
            UsageCounter.day == day,
            UsageCounter.key == key,
        )
        val = db.scalar(stmt)
        return val if val is not None else 0
