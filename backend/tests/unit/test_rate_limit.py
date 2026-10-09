"""Unit tests for core/rate_limit.py's per-IP bucket key behind Fly's proxy."""

from unittest.mock import MagicMock

import pytest
from fastapi import Request

from backend.core import rate_limit

PROXY_IP = "172.16.5.2"  # what request.client.host is behind Fly's proxy
VISITOR_IP = "203.0.113.7"


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/races",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (PROXY_IP, 51234),
        }
    )


def _on_fly(monkeypatch: pytest.MonkeyPatch, app_name: str) -> None:
    monkeypatch.setattr(rate_limit, "get_app_settings", lambda: MagicMock(fly_app_name=app_name))


@pytest.mark.unit
def test_on_fly_the_visitor_ip_comes_from_fly_client_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _on_fly(monkeypatch, "f1-strategy")

    key = rate_limit.rate_limit_key(_request({"Fly-Client-IP": VISITOR_IP}))

    assert key == f"ip:{VISITOR_IP}"


@pytest.mark.unit
def test_on_fly_without_the_header_the_connection_ip_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _on_fly(monkeypatch, "f1-strategy")
    assert rate_limit.rate_limit_key(_request({})) == f"ip:{PROXY_IP}"


@pytest.mark.unit
def test_off_fly_a_client_cannot_pick_its_bucket_with_the_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _on_fly(monkeypatch, "")

    key = rate_limit.rate_limit_key(_request({"Fly-Client-IP": VISITOR_IP}))

    assert key == f"ip:{PROXY_IP}"


@pytest.mark.unit
def test_a_signed_in_request_is_bucketed_by_user_not_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _on_fly(monkeypatch, "f1-strategy")
    monkeypatch.setattr(
        rate_limit, "decode_token", lambda token: {"type": "access", "sub": "user-123"}
    )

    key = rate_limit.rate_limit_key(
        _request({"Authorization": "Bearer token", "Fly-Client-IP": VISITOR_IP})
    )

    assert key == "user:user-123"


@pytest.mark.unit
def test_positions_poll_gets_a_higher_limit_for_a_signed_in_user() -> None:
    # The circuit map polls positions every 1 s on web and every 2 s on
    # desktop and mobile; one account on all three must stay under the limit.
    assert rate_limit.positions_rate_limit_value("user:abc") == "180/minute"
    assert rate_limit.rate_limit_value("user:abc") == "60/minute"


@pytest.mark.unit
def test_positions_poll_keeps_the_logged_out_limit() -> None:
    assert rate_limit.positions_rate_limit_value(f"ip:{VISITOR_IP}") == "10/minute"
