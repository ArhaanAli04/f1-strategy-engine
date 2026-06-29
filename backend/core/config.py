from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class DatabaseSettings(BaseSettings):
    model_config = _ENV

    database_url: str
    timescale_url: str


class RedisSettings(BaseSettings):
    model_config = _ENV

    redis_url: str


class AuthSettings(BaseSettings):
    model_config = _ENV

    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7


class AWSSettings(BaseSettings):
    model_config = _ENV

    aws_bucket_name: str = "f1-strategy-models"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-south-1"


class MLSettings(BaseSettings):
    model_config = _ENV

    fastf1_cache_dir: str = "/tmp/fastf1_cache"  # noqa: S108


class AppSettings(BaseSettings):
    model_config = _ENV

    sentry_dsn: str = ""
    environment: str = "development"
    fcm_server_key: str = ""


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
def get_app_settings() -> AppSettings:
    return AppSettings()
