import os
import sys
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, pool
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.ai.contracts import AIProvider
from app.ai.mock import MockProvider
from app.config import get_settings
from app.db import get_db
from app.deps import get_ai_provider
from app.main import create_app
from app.services.auth_service import AuthService

settings = get_settings()

# Refuse to run if test database name does not end in _test
parsed_url = make_url(settings.TEST_DATABASE_URL)
db_name = parsed_url.database
if not db_name or not db_name.endswith("_test"):
    raise RuntimeError(
        f"SAFETY CHECK FAILED: Refusing to run tests. "
        f"Test database name must end in '_test', got: '{db_name}'"
    )

# Run migrations once per test session on the real test database
@pytest.fixture(scope="session", autouse=True)
def apply_migrations():
    os.environ["ALEMBIC_DATABASE_URL"] = settings.TEST_DATABASE_URL
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", settings.TEST_DATABASE_URL)
    command.upgrade(alembic_cfg, "head")
    yield


test_engine = create_engine(
    settings.TEST_DATABASE_URL,
    poolclass=pool.NullPool,
    connect_args={"prepare_threshold": None, "client_encoding": "utf8"},
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """
    Each test runs inside an isolated database transaction with savepoint support,
    and is rolled back on teardown.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_get_db():
        yield db_session

    def override_get_ai():
        return MockProvider()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_ai_provider] = override_get_ai

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client: TestClient, db_session: Session) -> dict[str, str]:
    """Helper fixture to register an anonymous user and return authorization header."""
    auth_service = AuthService()
    raw_token, _ = auth_service.register_anonymous_user(db_session)
    return {"Authorization": f"Bearer {raw_token}"}
