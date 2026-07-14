import logging
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from backend.apis.v1 import api_v1_router
from backend.core.config import get_app_settings
from backend.core.database import get_engine
from backend.core.exceptions import (
    F1StrategyError,
    f1_strategy_error_handler,
    unhandled_error_handler,
)
from backend.core.middleware import RequestIDMiddleware, TimingMiddleware, register_cors
from backend.core.rate_limit import limiter
from backend.core.redis_client import _get_pool

logger = logging.getLogger(__name__)

# Sentry's performance tracing overhead isn't worth paying for at 100% in
# production traffic volume, but in development there's so little traffic
# that full tracing costs nothing and is more useful for debugging.
_PRODUCTION_TRACES_SAMPLE_RATE = 0.1
_NON_PRODUCTION_TRACES_SAMPLE_RATE = 1.0

_metrics_basic_auth = HTTPBasic()


def verify_metrics_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(_metrics_basic_auth)],
) -> None:
    """Guard /metrics with HTTP Basic Auth (METRICS_USER/METRICS_PASSWORD)."""
    settings = get_app_settings()
    valid_user = secrets.compare_digest(credentials.username, settings.metrics_user)
    valid_password = secrets.compare_digest(credentials.password, settings.metrics_password)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid metrics credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app_settings = get_app_settings()

    if app_settings.sentry_dsn:
        traces_sample_rate = (
            _PRODUCTION_TRACES_SAMPLE_RATE
            if app_settings.environment in ("production", "staging")
            else _NON_PRODUCTION_TRACES_SAMPLE_RATE
        )
        sentry_sdk.init(
            dsn=app_settings.sentry_dsn,
            environment=app_settings.environment,
            release=app_settings.release_version,
            traces_sample_rate=traces_sample_rate,
            integrations=[FastApiIntegration(), CeleryIntegration()],
        )
        logger.info(
            "Sentry initialised (env=%s, release=%s, traces_sample_rate=%s)",
            app_settings.environment,
            app_settings.release_version,
            traces_sample_rate,
        )

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection OK")
    except Exception as exc:
        logger.warning("Database connection failed on startup: %s", exc)

    import redis.asyncio as aioredis

    try:
        probe: aioredis.Redis = aioredis.Redis(connection_pool=_get_pool())  # type: ignore[type-arg]
        await probe.ping()
        await probe.aclose()  # type: ignore[attr-defined]
        logger.info("Redis connection OK")
    except Exception as exc:
        logger.warning("Redis connection failed on startup: %s", exc)

    yield

    logger.info("Shutting down — disposing DB engine")
    await get_engine().dispose()

    await _get_pool().disconnect()
    logger.info("Redis pool closed")


app = FastAPI(
    title="F1 Strategy Engine",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# --- rate limiting (per-route @limiter.limit decorators — see core/rate_limit.py) ---
app.state.limiter = limiter

# --- middleware (outermost first) ---
register_cors(app, allowed_origins=["*"])
app.add_middleware(RequestIDMiddleware)
app.add_middleware(TimingMiddleware)

# --- exception handlers ---
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_exception_handler(F1StrategyError, f1_strategy_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_error_handler)

# --- Prometheus metrics exposed at /metrics, gated by HTTP Basic Auth ---
Instrumentator().instrument(app).expose(app, dependencies=[Depends(verify_metrics_auth)])

# --- API routers ---
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/health", tags=["ops"])
async def health() -> JSONResponse:
    """Return DB and Redis connectivity status."""
    import redis.asyncio as aioredis

    db_ok = True
    redis_ok = True

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    try:
        probe: aioredis.Redis = aioredis.Redis(connection_pool=_get_pool())  # type: ignore[type-arg]
        await probe.ping()
        await probe.aclose()  # type: ignore[attr-defined]
    except Exception:
        redis_ok = False

    http_status = 503 if not db_ok else 200
    return JSONResponse(
        status_code=http_status,
        content={
            "status": "unhealthy" if not db_ok else ("ok" if redis_ok else "degraded"),
            "db": "ok" if db_ok else "error",
            "redis": "ok" if redis_ok else "error",
        },
    )
