from sqlalchemy import text
from sqlalchemy.orm import Session


class HealthRepository:
    # Bump with each migration. Readiness integration tests catch a stale revision.
    # Keep this independent of working directory and source-only migration files.
    required_revision = "d8a6b94f2c13"

    def schema_ready(self, db: Session) -> bool:
        actual = set(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
        return actual == {self.required_revision}

    def ping_database(self, db: Session) -> bool:
        result = db.execute(text("SELECT 1")).scalar()
        return result == 1


class HealthService:
    def __init__(self, health_repo: HealthRepository | None = None):
        self.health_repo = health_repo or HealthRepository()

    def check_db(self, db: Session) -> bool:
        return self.health_repo.ping_database(db)
