"""Redis-backed rate limiting: 60/min for authenticated requests, 10/min otherwise.

slowapi's `default_limits` on `SlowAPIMiddleware` cannot express a per-request
dynamic limit — verified against the installed slowapi 0.1.10 source
(slowapi/wrappers.py's LimitGroup.__iter__ and slowapi/extension.py's
_check_request_limit): a callable passed to `default_limits` is invoked via
`itertools.chain(*self._default_limits)` with no request ever bound
(`LimitGroup.request` stays None), so a callable keyed on auth status crashes
with "`request` object can't be None". Only the per-route `@limiter.limit(...)`
decorator calls `LimitGroup.with_request(request)` before iterating, so it's
the only path that can vary the limit by request. Every route in apis/v1/ is
therefore decorated individually with `rate_limit_value` rather than relying
on one global middleware default.

The decorator also requires the decorated endpoint to declare a `request:
Request` parameter (asserted in slowapi's source) — see apis/v1/auth.py,
races.py, and drivers.py.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter

from backend.core.config import get_app_settings, get_redis_settings
from backend.core.exceptions import AuthenticationError
from backend.core.security import decode_token

AUTHENTICATED_LIMIT = "60/minute"
UNAUTHENTICATED_LIMIT = "10/minute"


def _client_ip(request: Request) -> str:
    # Behind Fly's proxy request.client is the proxy, so every logged-out
    # visitor would share one bucket. Fly puts the real address in
    # Fly-Client-IP and overwrites any value a client sends, so the header is
    # trusted only when running on Fly (FLY_APP_NAME set); anywhere else a
    # client could choose its own bucket with it.
    if get_app_settings().fly_app_name:
        fly_client_ip = request.headers.get("fly-client-ip")
        if fly_client_ip:
            return fly_client_ip
    return request.client.host if request.client is not None else "unknown"


def rate_limit_key(request: Request) -> str:
    """Per-identity bucket key: the authenticated user's ID, or their client IP.

    Args:
        request: The incoming request. slowapi calls this itself, so it takes
            no other arguments (see LimitGroup's `key_function` requirement).
    Returns:
        "user:{user_id}" if the request carries a valid access token,
        otherwise "ip:{client_ip}".
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[len("bearer ") :]
        try:
            payload = decode_token(token)
        except AuthenticationError:
            return f"ip:{_client_ip(request)}"
        if payload.get("type") == "access":
            return f"user:{payload['sub']}"
    return f"ip:{_client_ip(request)}"


def rate_limit_value(key: str) -> str:
    """Resolve the rate-limit string for a bucket key produced by rate_limit_key.

    Args:
        key: A key produced by rate_limit_key. slowapi calls this itself with
            that key (see LimitGroup.__iter__'s "key" parameter convention),
            so it takes no other arguments.
    Returns:
        "60/minute" for an authenticated user's bucket, "10/minute" for an IP bucket.
    """
    return AUTHENTICATED_LIMIT if key.startswith("user:") else UNAUTHENTICATED_LIMIT


limiter = Limiter(key_func=rate_limit_key, storage_uri=get_redis_settings().redis_url)
