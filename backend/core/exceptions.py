import logging
from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class F1StrategyError(Exception):
    """Base exception for all F1 Strategy Engine errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class NotFoundError(F1StrategyError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "NOT_FOUND"


class TelemetryNotAvailableError(F1StrategyError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "TELEMETRY_UNAVAILABLE"


class ModelNotLoadedError(F1StrategyError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "MODEL_NOT_LOADED"


class AuthenticationError(F1StrategyError):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "AUTHENTICATION_FAILED"


class AuthorizationError(F1StrategyError):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "AUTHORIZATION_FAILED"


class ValidationError(F1StrategyError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "VALIDATION_ERROR"


async def f1_strategy_error_handler(request: Request, exc: F1StrategyError) -> JSONResponse:
    logger.error(
        "F1StrategyError [%s] on %s: %s",
        exc.error_code,
        request.url.path,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error_code, "message": exc.message, "detail": exc.detail},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "detail": None,
        },
    )
