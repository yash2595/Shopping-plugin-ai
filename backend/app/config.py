from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENV: str = "development"
    DATABASE_URL: str = "postgresql+psycopg://postgres@127.0.0.1:5433/shopping"
    DIRECT_DATABASE_URL: str = "postgresql+psycopg://postgres@127.0.0.1:5433/shopping"
    TEST_DATABASE_URL: str = "postgresql+psycopg://postgres@127.0.0.1:5433/shopping_test"

    # Database connection pool configuration
    DB_POOL_SIZE: int = 5  # PLACEHOLDER: small pool size for Neon pooled connection
    DB_MAX_OVERFLOW: int = 10  # PLACEHOLDER: max overflow connections
    DB_POOL_TIMEOUT: int = 30  # PLACEHOLDER: pool timeout in seconds

    # Authentication
    AUTH_TOKEN_EXPIRE_DAYS: int = 30  # PLACEHOLDER: anonymous bearer token validity in days

    # Business thresholds
    PRICE_OBSERVATION_DEDUP_HOURS: int = 6  # PLACEHOLDER: skip identical price within 6 hours
    PRICE_CHANGE_PENDING_THRESHOLD: float = 0.50  # PLACEHOLDER: flag price jump > 50% as pending
    MIN_HISTORY_SPAN_DAYS: int = 7  # PLACEHOLDER: minimum span between observations in days

    # Rate Limiting & Observation Trust
    MAX_OBSERVATIONS_PER_LISTING_PER_HOUR: int = 10  # PLACEHOLDER: max observations per user per listing per hour
    MAX_OBSERVATIONS_PER_USER_PER_DAY: int = 100  # PLACEHOLDER: max observations per user per day
    MAX_FUTURE_TIMESTAMP_MINUTES: int = 5  # PLACEHOLDER: max clock skew in the future
    MAX_AI_CALLS_PER_USER_PER_DAY: int = 50  # PLACEHOLDER: daily AI calls per user

    # Policy & Context Limits
    MAX_ACTIVE_ALERTS_PER_USER: int = 20  # PLACEHOLDER: max active alerts per user
    PREFERENCE_WHITELIST: tuple[str, ...] = (
        "color",
        "size",
        "brand",
        "category",
        "delivery_speed",
        "max_delivery_days",
    )
    MAX_PREFERENCE_STRING_LENGTH: int = 50  # PLACEHOLDER: max length of preference string values
    MAX_PREFERENCE_INT_VALUE: int = 365  # PLACEHOLDER: max integer preference value

    # AI Configuration
    AI_PROVIDER: str = "mock"  # 'mock' | 'gemini'
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"  # PLACEHOLDER
    AI_TIMEOUT_SECONDS: float = 5.0  # PLACEHOLDER: timeout for AI provider calls
    CHAT_RECENT_MESSAGES_COUNT: int = 10  # PLACEHOLDER: number of recent messages for AI context

    # Reviews Configuration
    MAX_REVIEWS_PER_SUMMARY: int = 30  # PLACEHOLDER: max reviews in single summary
    MAX_REVIEW_LENGTH_CHARS: int = 1000  # PLACEHOLDER: max length of single review text
    MAX_TOTAL_REVIEWS_CHARS: int = 15000  # PLACEHOLDER: max combined chars of all reviews
    MIN_USABLE_REVIEWS: int = 3  # PLACEHOLDER: minimum usable reviews to generate summary
    REVIEW_SUMMARY_CACHE_DAYS: int = 7  # PLACEHOLDER: cache summary freshness in days

    # History Resolution
    MAX_HISTORY_DAILY_POINTS: int = 365  # PLACEHOLDER: maximum daily points returned

    # Maintenance & Jobs
    JOB_SECRET: str = "dev-job-secret-token"
    SNAPSHOT_RETENTION_DAYS: int = 30  # PLACEHOLDER: snapshot retention period in days
    MESSAGE_RETENTION_DAYS: int = 30  # PLACEHOLDER: message retention period in days
    NOTIFICATION_RETENTION_DAYS: int = 90  # PLACEHOLDER: notification retention period in days
    SESSION_INACTIVE_RETENTION_DAYS: int = 90  # PLACEHOLDER: session retention period in days
    REVIEW_SUMMARY_RETENTION_DAYS: int = 30  # PLACEHOLDER: review summary retention period in days

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
