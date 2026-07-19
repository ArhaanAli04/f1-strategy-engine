"""Unit tests for Pydantic request/response schema validation."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.schemas.simulate_schema import SimulateStrategyRequest
from backend.schemas.strategy_schema import StrategyPredictionResponse
from backend.schemas.telemetry_schema import LiveTelemetryEvent


@pytest.mark.unit
def test_simulate_request_validates_compound_list() -> None:
    with pytest.raises(ValidationError):
        SimulateStrategyRequest(
            driver_id=uuid.uuid4(),
            current_lap=10,
            current_compound="MEDIUM",
            current_tyre_age=10,
            remaining_laps=40,
            compounds=["INVALID"],
        )


@pytest.mark.unit
def test_strategy_response_serialises_to_json() -> None:
    response = StrategyPredictionResponse(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        driver_id=uuid.uuid4(),
        predicted_at=datetime.now(UTC),
        optimal_pit_lap=22,
        pit_probability=0.8,
        undercut_score=0.63,
        overcut_score=0.37,
        tire_life_remaining=8.0,
        confidence_score=0.75,
        model_version="production",
        created_at=datetime.now(UTC),
    )

    parsed = json.loads(response.model_dump_json())

    assert parsed["optimal_pit_lap"] == 22
    assert parsed["model_version"] == "production"


@pytest.mark.unit
def test_live_telemetry_event_timestamp_required() -> None:
    with pytest.raises(ValidationError):
        LiveTelemetryEvent(  # type: ignore[call-arg]  # intentionally omits required timestamp_ms
            driver_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            speed_kmh=250.0,
            throttle_pct=80.0,
            brake=False,
            gear=6,
            drs=False,
        )
