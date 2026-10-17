"""Unit tests for check_simulator_scenarios' helpers and scenario checks (the
simulations themselves need the database and the models)."""

import pytest

from backend.scripts import check_simulator_scenarios as cs
from backend.scripts.evaluate_simulator import LapRow
from backend.services.ml.race_simulator import DriverPositionDistribution, RaceSimulationResult


def _distribution(
    driver_id: str, finish: float, position: float, ahead: dict[str, float] | None = None
) -> DriverPositionDistribution:
    return DriverPositionDistribution(
        driver_id=driver_id,
        position_probabilities={round(position): 1.0},
        mean_position=position,
        mean_finish_time_seconds=finish,
        finish_time_p5_seconds=finish - 5,
        finish_time_p95_seconds=finish + 5,
        finish_ahead_probability=ahead or {},
    )


def _run(
    by_label: dict[str, tuple[float, float, dict[str, float]]],
    others: dict[str, float] | None = None,
) -> cs.ScenarioRun:
    """A run of the tested driver "me": label -> (finish, position, ahead
    probabilities by driver id); others: rival id -> mean position, the same in
    every plan."""
    results = {
        label: RaceSimulationResult(
            n_simulations=1,
            driver_distributions=[
                _distribution("me", finish, position, ahead),
                *(
                    _distribution(rival, 5000.0, rival_position)
                    for rival, rival_position in (others or {}).items()
                ),
            ],
        )
        for label, (finish, position, ahead) in by_label.items()
    }
    return cs.ScenarioRun("me", results, {"ME": "me", "RIV": "riv"}, {"RIV": 2})


@pytest.mark.unit
def test_real_plan_charges_a_stop_under_way_to_the_first_simulated_lap() -> None:
    laps = [
        LapRow(19, "MEDIUM", 19, 1),
        LapRow(20, "MEDIUM", 20, 1),  # in-lap: under way at the start lap
        LapRow(21, "HARD", 1, 2),
        LapRow(30, "HARD", 10, 2),
        LapRow(31, "HARD", 11, 2),  # in-lap of the second stop
        LapRow(32, "MEDIUM", 1, 2),
    ]
    assert cs._real_plan(laps, start_lap=20) == ((21, "HARD"), (31, "MEDIUM"))
    assert cs._real_plan(laps, start_lap=19) == ((20, "HARD"), (31, "MEDIUM"))


@pytest.mark.unit
def test_sweep_plans_move_only_the_first_stop() -> None:
    plans = cs._sweep(17, 19, 52)
    assert [p.label for p in plans] == ["first stop L17", "first stop L18", "first stop L19"]
    assert all(p.stops is not None and p.stops[1] == (52, "MEDIUM") for p in plans)
    assert [p.stops[0][0] for p in plans if p.stops] == [17, 18, 19]


@pytest.mark.unit
def test_sweep_is_smooth_allows_at_most_3_s_between_neighbouring_laps() -> None:
    smooth = _run({"a": (5000.0, 1, {}), "b": (5002.5, 1, {}), "c": (5001.0, 1, {})})
    jumpy = _run({"a": (5000.0, 1, {}), "b": (5004.0, 1, {})})
    assert cs._sweep_is_smooth(smooth)
    assert not cs._sweep_is_smooth(jumpy)


@pytest.mark.unit
def test_scenario_run_reads_the_tested_driver_and_named_cars() -> None:
    run = _run({"real": (5010.0, 2.5, {"riv": 0.7})}, others={"riv": 3.1})
    assert run.finish("real") == 5010.0
    assert run.position("real") == 2.5
    assert run.position("real", "RIV") == 3.1
    assert run.ahead_of("real", "RIV") == 0.7
    assert run.ahead_of("real", "ME") == 0.0  # no entry: never ahead of itself


def _scenario(name: str) -> cs.Scenario:
    return next(s for s in cs.SCENARIOS if s.name == name)


@pytest.mark.unit
def test_one_stop_scenario_passes_only_when_the_real_two_stop_is_faster() -> None:
    scenario = _scenario("1-stop in a 2-stop race")
    labels = [p.label for p in scenario.plans]
    two_stop_faster = _run(
        {label: (5000.0 if label == "real" else 5010.0, 1, {}) for label in labels}
    )
    one_stop_faster = _run(
        {label: (5010.0 if label == "real" else 5000.0, 1, {}) for label in labels}
    )
    assert scenario.check(two_stop_faster)
    assert not scenario.check(one_stop_faster)


@pytest.mark.unit
def test_last_lap_stop_scenario_expects_about_one_stop_of_time() -> None:
    scenario = _scenario("Stop on the last lap")

    def run(extra: float) -> cs.ScenarioRun:
        return _run(
            {
                "no further stop": (5000.0, 1, {}),
                "stop L44 (last lap)": (5000.0 + extra, 2, {}),
            }
        )

    assert scenario.check(run(22.0))
    assert not scenario.check(run(5.0))
    assert not scenario.check(run(40.0))


@pytest.mark.unit
def test_every_scenario_check_uses_only_its_own_plans_and_cars() -> None:
    """Each check runs on a fake run holding exactly the scenario's plans and
    the cars it names, so a misspelt plan label or car code fails here
    instead of in a 10-minute run."""
    for scenario in cs.SCENARIOS:
        codes = {scenario.driver, *(c for c in [scenario.rival] if c), *cs.TOP_FIVE_AUSTRIA}
        ids = {code: code.lower() for code in codes}
        results = {
            plan.label: RaceSimulationResult(
                n_simulations=1,
                driver_distributions=[
                    _distribution(ids[code], 5000.0, float(i + 1)) for i, code in enumerate(codes)
                ],
            )
            for plan in scenario.plans
        }
        run = cs.ScenarioRun(
            ids[scenario.driver], results, ids, {code: i + 1 for i, code in enumerate(codes)}
        )
        assert isinstance(scenario.check(run), bool), scenario.name
