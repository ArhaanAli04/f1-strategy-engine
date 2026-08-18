from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

_MIN_SECRET_KEY_LENGTH = 32

# Known-weak SECRET_KEY values to reject outright, regardless of length —
# includes the .env.example placeholder itself, so copying that file
# without generating a real key fails loudly instead of shipping a
# guessable production secret.
_WEAK_SECRET_KEYS = frozenset(
    {
        "secret",
        "changeme",
        "your-secret-key-here",
        "dev",
        "test",
        "password",
        "supersecret",
        "f1strategy",
        "development",
        "change-me-to-a-256-bit-random-hex-string",
    }
)


class DatabaseSettings(BaseSettings):
    model_config = _ENV

    database_url: str
    timescale_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 20


class RedisSettings(BaseSettings):
    model_config = _ENV

    redis_url: str


class AuthSettings(BaseSettings):
    model_config = _ENV

    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    @field_validator("secret_key")
    @classmethod
    def _validate_secret_key(cls, value: str) -> str:
        if len(value) < _MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {_MIN_SECRET_KEY_LENGTH} characters "
                f"(got {len(value)}). Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if value.strip().lower() in _WEAK_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY matches a known-weak default value. Generate a real one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return value


class AWSSettings(BaseSettings):
    model_config = _ENV

    aws_bucket_name: str = "f1-strategy-models"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-south-1"


class MLSettings(BaseSettings):
    model_config = _ENV

    fastf1_cache_dir: str = "/tmp/fastf1_cache"  # noqa: S108
    model_cache_dir: str = "/tmp/f1_models"  # noqa: S108


class LiveTimingSettings(BaseSettings):
    model_config = _ENV

    # F1's live timing feed requires an F1TV Access/Pro/Premium subscription
    # and a one-time interactive browser auth (see fastf1.internals.f1auth).
    # Default to unauthenticated (partial/best-effort data) until a
    # subscription token is set up; flip this once one is.
    f1tv_authenticated: bool = False


class AppSettings(BaseSettings):
    model_config = _ENV

    sentry_dsn: str = ""
    environment: str = "development"
    release_version: str = "0.1.0"
    fcm_server_key: str = ""
    # firebase-admin's messaging API requires a service-account JSON
    # (Google retired the legacy FCM_SERVER_KEY HTTP API in June 2024).
    firebase_credentials_path: str = ""
    # Local dev defaults so the stack works without .env entries — Day 19
    # replaces these with real values via GitHub Secrets in production.
    metrics_user: str = "metrics"
    metrics_password: str = "metrics-dev"  # noqa: S105
    # "*" for local dev (works out of the box). Production must set a real
    # comma-separated allowlist via the ALLOWED_ORIGINS env var — CORS with
    # allow_credentials=True and origin "*" is rejected by browsers anyway,
    # so "*" is only ever meaningful in development.
    allowed_origins: str = "*"

    @property
    def allowed_origins_list(self) -> list[str]:
        if self.allowed_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_db_settings() -> DatabaseSettings:
    return DatabaseSettings()  # type: ignore[call-arg]


@lru_cache
def get_redis_settings() -> RedisSettings:
    return RedisSettings()  # type: ignore[call-arg]


@lru_cache
def get_auth_settings() -> AuthSettings:
    return AuthSettings()  # type: ignore[call-arg]


@lru_cache
def get_aws_settings() -> AWSSettings:
    return AWSSettings()


@lru_cache
def get_ml_settings() -> MLSettings:
    return MLSettings()


@lru_cache
def get_live_timing_settings() -> LiveTimingSettings:
    return LiveTimingSettings()


@lru_cache
def get_app_settings() -> AppSettings:
    return AppSettings()
