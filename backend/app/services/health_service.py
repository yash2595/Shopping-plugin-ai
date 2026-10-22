from sqlalchemy import text
from sqlalchemy.orm import Session


class HealthRepository:
    def ping_database(self, db: Session) -> bool:
        result = db.execute(text("SELECT 1")).scalar()
        return result == 1


class HealthService:
    def __init__(self, health_repo: HealthRepository | None = None):
        self.health_repo = health_repo or HealthRepository()

    def check_db(self, db: Session) -> bool:
        return self.health_repo.ping_database(db)
