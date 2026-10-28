from functools import lru_cache
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENV: Literal["development", "test", "production"] = "development"
    DATABASE_URL: str = Field(
        default="postgresql+psycopg://postgres@127.0.0.1:5433/shopping", repr=False
    )
    DIRECT_DATABASE_URL: str = Field(
        default="postgresql+psycopg://postgres@127.0.0.1:5433/shopping", repr=False
    )
    TEST_DATABASE_URL: str = Field(
        default="postgresql+psycopg://postgres@127.0.0.1:5433/shopping_test", repr=False
    )

    # Database connection pool configuration
    DB_POOL_SIZE: int = 5  # PLACEHOLDER: small pool size for Neon pooled connection
    DB_MAX_OVERFLOW: int = 10  # PLACEHOLDER: max overflow connections
    DB_POOL_TIMEOUT: int = 30  # PLACEHOLDER: pool timeout in seconds

    # Authentication
    AUTH_TOKEN_EXPIRE_DAYS: int = Field(default=30, ge=1, le=365)
    AUTH_MODE: Literal["anonymous", "oidc"] = "anonymous"
    AUTH_ISSUER: str = ""
    AUTH_AUDIENCE: str = ""
    AUTH_JWKS_JSON: str = Field(default="", repr=False)
    ALLOW_ANONYMOUS_REGISTRATION: bool = False  # Explicit opt-in required in production.

    # Business thresholds
    PRICE_OBSERVATION_DEDUP_HOURS: int = 6  # PLACEHOLDER: skip identical price within 6 hours
    PRICE_CHANGE_PENDING_THRESHOLD: float = 0.50  # PLACEHOLDER: flag price jump > 50% as pending
    MIN_HISTORY_SPAN_DAYS: int = 7  # PLACEHOLDER: minimum span between observations in days

    # Rate Limiting & Observation Trust
    MAX_OBSERVATIONS_PER_LISTING_PER_HOUR: int = (
        10  # PLACEHOLDER: max observations per user per listing per hour
    )
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
    AI_PROVIDER: Literal["mock", "gemini"] = "mock"
    GEMINI_API_KEY: Optional[str] = Field(default=None, repr=False)
    GEMINI_MODEL: str = "gemini-2.5-flash"  # PLACEHOLDER
    AI_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0, le=120)
    AI_MAX_CONCURRENT_CALLS: int = Field(default=8, ge=1, le=64)
    AI_RUNTIME_ENABLED: bool = False
    AI_RUNTIME_PROVIDER: Literal["mock", "chat", "local"] = "mock"
    AI_ANALYSIS_ONLY: bool = True
    AI_LOCAL_ENDPOINT: str = "http://127.0.0.1:11434/v1/chat/completions"
    AI_LOCAL_MODEL: str = "llama3.2"
    AI_LOCAL_TIMEOUT_SECONDS: float = Field(default=5, ge=0.1, le=60)
    AI_LOCAL_FALLBACK: Literal["deterministic", "cloud"] = "deterministic"
    AI_RUNTIME_TIMEOUT_SECONDS: float = Field(default=90, ge=5, le=300)
    AI_CORE_TIMEOUT_SECONDS: float = Field(default=30, ge=1, le=60)
    AI_MAX_STEPS: int = Field(default=8, ge=1, le=30)
    AI_MAX_SNAPSHOT_AGE_SECONDS: int = Field(default=120, ge=1, le=3600)
    AI_SEARCH_TIMEOUT_SECONDS: int = Field(default=120, ge=1, le=600)
    AI_BROWSER_ORIGINS: tuple[str, ...] = (
        "https://www.amazon.in", "https://amazon.in", "https://www.flipkart.com", "https://flipkart.com",
    )
    AI_MUTATIONS_ENABLED: bool = False
    AI_SEARCH_STORES: list[dict] = Field(default_factory=list)
    AI_CHAT_ENDPOINT: str = ""
    AI_CHAT_MODEL: str = ""
    AI_CHAT_API_KEY: Optional[str] = Field(default=None, repr=False)
    AI_STT_ENDPOINT: str = ""
    AI_STT_MODEL: str = ""
    AI_STT_API_KEY: Optional[str] = Field(default=None, repr=False)
    AI_TTS_ENDPOINT: str = ""
    AI_TTS_MODEL: str = ""
    AI_TTS_API_KEY: Optional[str] = Field(default=None, repr=False)
    AI_TTS_VOICE: str = ""
    AI_TTS_FORMAT: Literal["mp3", "wav"] = "mp3"
    CORS_ALLOWED_ORIGINS: tuple[str, ...] = ()
    MAX_SPEECH_CALLS_PER_USER_PER_DAY: int = Field(default=100, ge=1, le=10000)
    CHAT_RECENT_MESSAGES_COUNT: int = Field(default=10, ge=1, le=50)

    # Reviews Configuration
    MAX_REVIEWS_PER_SUMMARY: int = 30  # PLACEHOLDER: max reviews in single summary
    MAX_REVIEW_LENGTH_CHARS: int = 1000  # PLACEHOLDER: max length of single review text
    MAX_TOTAL_REVIEWS_CHARS: int = 15000  # PLACEHOLDER: max combined chars of all reviews
    MIN_USABLE_REVIEWS: int = 3  # PLACEHOLDER: minimum usable reviews to generate summary
    REVIEW_SUMMARY_CACHE_DAYS: int = 7  # PLACEHOLDER: cache summary freshness in days

    # History Resolution
    MAX_HISTORY_DAILY_POINTS: int = 365  # PLACEHOLDER: maximum daily points returned

    # Maintenance & Jobs
    JOB_SECRET: str = Field(default="dev-job-secret-token", repr=False)
    SNAPSHOT_RETENTION_DAYS: int = 30  # PLACEHOLDER: snapshot retention period in days
    MESSAGE_RETENTION_DAYS: int = 30  # PLACEHOLDER: message retention period in days
    NOTIFICATION_RETENTION_DAYS: int = 90  # PLACEHOLDER: notification retention period in days
    SESSION_INACTIVE_RETENTION_DAYS: int = 90  # PLACEHOLDER: session retention period in days
    REVIEW_SUMMARY_RETENTION_DAYS: int = 30  # PLACEHOLDER: review summary retention period in days

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def production_secrets(self):
        if self.AUTH_MODE == "oidc":
            issuer = urlsplit(self.AUTH_ISSUER)
            if (
                issuer.scheme != "https"
                or not issuer.hostname
                or len(self.AUTH_ISSUER) > 512
                or not self.AUTH_ISSUER.isascii()
                or issuer.username
                or issuer.password
                or issuer.query
                or issuer.fragment
                or not self.AUTH_AUDIENCE
                or not self.AUTH_JWKS_JSON
            ):
                raise ValueError("OIDC requires an HTTPS issuer, API audience and public JWKS")
        for origin in self.CORS_ALLOWED_ORIGINS:
            parsed = urlsplit(origin)
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.scheme not in {"https", "http", "chrome-extension"}
                or parsed.scheme == "http"
                and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError(
                    "CORS origins must be explicit HTTPS, extension, or localhost origins"
                )
        if self.ENV == "production" and (
            self.JOB_SECRET == "dev-job-secret-token" or len(self.JOB_SECRET) < 32
        ):
            raise ValueError("Production requires a separately configured strong JOB_SECRET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
