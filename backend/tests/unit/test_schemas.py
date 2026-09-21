"""Unit tests for Pydantic request/response schema validation."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.schemas.simulate_schema import ScenarioPlan, SimulateStrategyRequest
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


# --- SimulateStrategyRequest bounds (current_lap/remaining_laps/current_tyre_age
# minimums, pit_laps horizon) — see
# docs/simulator-issues-wet-model-and-position-context.md's Checkpoint-6
# follow-up: a request with no bounds at all let a 68-lap what-if run against
# a 44-lap race. The current_lap-vs-real-session-progress check itself needs a
# DB lookup (strategy_service.validate_current_lap, covered in
# test_strategy_service.py) — these tests cover only what Pydantic alone can
# enforce, statically, from the request body.


def _build_request(
    *,
    current_lap: int = 10,
    current_compound: str = "MEDIUM",
    current_tyre_age: int = 10,
    remaining_laps: int = 40,
    pit_laps: list[int] | None = None,
    compounds: list[str] | None = None,
    scenarios: list[ScenarioPlan] | None = None,
) -> SimulateStrategyRequest:
    return SimulateStrategyRequest(
        driver_id=uuid.uuid4(),
        current_lap=current_lap,
        current_compound=current_compound,
        current_tyre_age=current_tyre_age,
        remaining_laps=remaining_laps,
        pit_laps=pit_laps if pit_laps is not None else [],
        compounds=compounds if compounds is not None else [],
        scenarios=scenarios,
    )


@pytest.mark.unit
def test_simulate_request_rejects_current_lap_below_one() -> None:
    with pytest.raises(ValidationError):
        _build_request(current_lap=0)


@pytest.mark.unit
def test_simulate_request_rejects_non_positive_remaining_laps() -> None:
    with pytest.raises(ValidationError):
        _build_request(remaining_laps=0)


@pytest.mark.unit
def test_simulate_request_rejects_negative_tyre_age() -> None:
    with pytest.raises(ValidationError):
        _build_request(current_tyre_age=-1)


@pytest.mark.unit
def test_simulate_request_accepts_zero_tyre_age() -> None:
    request = _build_request(current_tyre_age=0)
    assert request.current_tyre_age == 0


@pytest.mark.unit
def test_simulate_request_rejects_pit_lap_at_or_before_current_lap() -> None:
    with pytest.raises(ValidationError):
        _build_request(current_lap=10, remaining_laps=5, pit_laps=[10], compounds=["HARD"])


@pytest.mark.unit
def test_simulate_request_rejects_pit_lap_beyond_horizon() -> None:
    with pytest.raises(ValidationError):
        _build_request(current_lap=10, remaining_laps=5, pit_laps=[16], compounds=["HARD"])


@pytest.mark.unit
def test_simulate_request_accepts_pit_lap_at_horizon_boundaries() -> None:
    # current_lap + 1 (earliest valid) and current_lap + remaining_laps (latest
    # valid) are both inclusive boundaries, not off-by-one exclusions.
    request = _build_request(
        current_lap=10, remaining_laps=5, pit_laps=[11, 15], compounds=["MEDIUM", "HARD"]
    )
    assert request.pit_laps == [11, 15]


# --- SimulateStrategyRequest.scenarios (What-If Simulator multi-scenario
# rebuild, Checkpoint 3 — see docs/core-feature-rebuild-whatif-simulator.md) ---


@pytest.mark.unit
def test_scenario_plan_rejects_mismatched_pit_laps_and_compounds() -> None:
    with pytest.raises(ValidationError):
        ScenarioPlan(pit_laps=[30, 33], compounds=["HARD"])


@pytest.mark.unit
def test_scenario_plan_rejects_unknown_compound() -> None:
    with pytest.raises(ValidationError):
        ScenarioPlan(pit_laps=[30], compounds=["ROCKET_FUEL"])


@pytest.mark.unit
def test_scenario_plan_accepts_empty_pit_laps_as_autonomous() -> None:
    # Same convention as the top-level plan: empty pit_laps means "let the
    # simulation decide pit timing for this scenario" — a legitimate
    # baseline scenario in a compare request (e.g. "no pit" vs two candidate
    # pit laps), not a malformed one.
    scenario = ScenarioPlan(pit_laps=[], compounds=[], label="No pit")
    assert scenario.pit_laps == []


@pytest.mark.unit
def test_simulate_request_rejects_scenarios_alongside_top_level_plan() -> None:
    with pytest.raises(ValidationError):
        _build_request(
            pit_laps=[15],
            compounds=["HARD"],
            scenarios=[ScenarioPlan(pit_laps=[30], compounds=["HARD"])],
        )


@pytest.mark.unit
def test_simulate_request_rejects_empty_scenarios_list() -> None:
    # An explicit [] is meaningless (nothing to compare) — distinct from
    # omitting scenarios entirely (None), which is the ordinary single-plan
    # request and must keep working unchanged.
    with pytest.raises(ValidationError):
        _build_request(scenarios=[])


@pytest.mark.unit
def test_simulate_request_rejects_more_than_max_scenarios() -> None:
    with pytest.raises(ValidationError):
        _build_request(
            current_lap=10,
            remaining_laps=40,
            scenarios=[ScenarioPlan(pit_laps=[11 + i], compounds=["HARD"]) for i in range(5)],
        )


@pytest.mark.unit
def test_simulate_request_rejects_scenario_pit_lap_beyond_horizon() -> None:
    with pytest.raises(ValidationError):
        _build_request(
            current_lap=10,
            remaining_laps=5,
            scenarios=[
                ScenarioPlan(pit_laps=[12], compounds=["HARD"]),
                ScenarioPlan(pit_laps=[99], compounds=["HARD"]),  # beyond horizon_end=15
            ],
        )


@pytest.mark.unit
def test_simulate_request_accepts_valid_scenarios() -> None:
    request = _build_request(
        current_lap=28,
        remaining_laps=30,
        scenarios=[
            ScenarioPlan(pit_laps=[30], compounds=["HARD"], label="Pit lap 30"),
            ScenarioPlan(pit_laps=[33], compounds=["HARD"], label="Pit lap 33"),
            ScenarioPlan(pit_laps=[36], compounds=["HARD"], label="Pit lap 36"),
        ],
    )
    assert request.scenarios is not None
    assert len(request.scenarios) == 3
    assert request.pit_laps == []
    assert [s.label for s in request.scenarios] == ["Pit lap 30", "Pit lap 33", "Pit lap 36"]


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
