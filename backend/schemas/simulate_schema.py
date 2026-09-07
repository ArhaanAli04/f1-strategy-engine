import uuid

from pydantic import BaseModel, Field, model_validator

# Matches strategy_service._COMPOUND_ENCODING / prediction_worker._COMPOUND_ENCODING's
# key set — the only compounds any tire_deg pipeline was ever trained on.
_KNOWN_COMPOUNDS = frozenset({"HARD", "INTERMEDIATE", "MEDIUM", "SOFT", "WET"})

# Cap on SimulateStrategyRequest.scenarios — each entry costs one full
# race_simulator.simulate_race call server-side (Checkpoint 1's memoization
# fix brought that to ~7-10s on a real ~22-driver field; see
# docs/core-feature-rebuild-whatif-simulator.md). 4 scenarios keeps a compare
# request comfortably inside useSimulationResult's 60s client poll timeout
# even under worst-case per-call cost.
_MAX_SCENARIOS = 4


def _validate_plan_compounds(pit_laps: list[int], compounds: list[str]) -> None:
    """Shared pit_laps/compounds validation for one candidate plan.

    Used by both SimulateStrategyRequest's own top-level pit_laps/compounds
    (a single plan) and each ScenarioPlan in a multi-scenario compare
    request — the length-match and known-compound rules are identical for
    either; only the current_lap/remaining_laps horizon check differs (it
    needs fields that live on the parent request, not on ScenarioPlan
    itself, so that check stays in SimulateStrategyRequest._validate_pit_plan).

    Args:
        pit_laps: A candidate plan's forced pit laps.
        compounds: Same length as pit_laps when pit_laps is non-empty.
    Raises:
        ValueError: length mismatch, or a compound outside _KNOWN_COMPOUNDS.
    """
    if pit_laps and len(pit_laps) != len(compounds):
        raise ValueError(
            f"pit_laps ({len(pit_laps)}) and compounds ({len(compounds)}) "
            "must be the same length when pit_laps is non-empty"
        )
    unknown = set(compounds) - _KNOWN_COMPOUNDS
    if unknown:
        raise ValueError(f"Unknown compound(s): {sorted(unknown)}")


class ScenarioPlan(BaseModel):
    """One candidate pit plan within a multi-scenario compare request.

    Same shape as SimulateStrategyRequest's own top-level pit_laps/compounds
    pair (a full, possibly multi-stop, plan for the requesting driver) —
    repeated up to _MAX_SCENARIOS times so POST /simulate can run several
    candidate plans against the IDENTICAL field state in one Celery task
    (one _build_race_state call, N race_simulator.simulate_race calls)
    instead of N separate round trips. This is the server-orchestrated
    design from docs/core-feature-rebuild-whatif-simulator.md — chosen over
    client-orchestrated N requests because polling N tasks at
    useSimulationResult's 2s interval would blow past the 60/minute
    authenticated rate limit (core/rate_limit.py), and because
    _build_race_state's DB queries are otherwise repeated N times for
    identical data.

    label is optional and display-only — the backend never interprets it,
    only passes it through verbatim onto the matching SimulatedRaceOutcome
    so the frontend can render "Pit lap 30" etc. without having to
    re-derive a label from pit_laps itself.
    """

    pit_laps: list[int] = []
    compounds: list[str] = []
    label: str | None = None

    @model_validator(mode="after")
    def _validate_compounds(self) -> "ScenarioPlan":
        _validate_plan_compounds(self.pit_laps, self.compounds)
        return self


class SimulateStrategyRequest(BaseModel):
    driver_id: uuid.UUID
    # >= 1, not >= 0: a session with zero ingested laps yet is a genuine
    # pre-race what-if (see strategy_service.validate_current_lap), but "lap
    # 0" itself isn't a meaningful race state to simulate from — the earliest
    # is "currently on lap 1". The actual upper bound (this can't exceed the
    # session's real progress by more than one lap) needs a DB lookup and is
    # enforced at request time by strategy_service.validate_current_lap, not
    # here — see docs/simulator-issues-wet-model-and-position-context.md's
    # Checkpoint-6 follow-up finding.
    current_lap: int = Field(ge=1)
    current_compound: str
    # 0 is a fresh tyre, not invalid — unlike current_lap/remaining_laps.
    current_tyre_age: int = Field(ge=0)
    remaining_laps: int = Field(ge=1)
    # Empty (default): the Monte Carlo simulation decides pit timing for this
    # driver autonomously, same as every other driver in the field. Non-empty:
    # forces this driver's simulated pit stops onto these exact laps — the
    # what-if scenario race_simulator.simulate_race's forced_pit_laps override
    # implements (see race_simulator.py). Mutually exclusive with scenarios
    # below — set one or the other, not both.
    pit_laps: list[int] = []
    # Compound to switch to after the pit stop at the same-index entry in
    # pit_laps — must be the same length as pit_laps when pit_laps is non-empty.
    compounds: list[str] = []
    # None (default): single-plan request, exactly as before this field
    # existed — pit_laps/compounds above describe the one plan to simulate.
    # Non-None: multi-scenario compare (Checkpoint 3) — pit_laps/compounds
    # above must then be left empty (enforced below); each ScenarioPlan is
    # simulated independently against the same field state, and the response's
    # strategies list carries one SimulatedRaceOutcome per scenario, in order.
    scenarios: list[ScenarioPlan] | None = Field(
        default=None, min_length=1, max_length=_MAX_SCENARIOS
    )

    @model_validator(mode="after")
    def _validate_pit_plan(self) -> "SimulateStrategyRequest":
        if self.scenarios is not None and (self.pit_laps or self.compounds):
            raise ValueError(
                "Set either pit_laps/compounds (a single plan) or scenarios "
                "(multi-scenario compare), not both."
            )

        _validate_plan_compounds(self.pit_laps, self.compounds)

        # A forced pit lap outside the simulated horizon was previously
        # silently ignored — race_simulator.simulate_race only ever checks
        # `if lap_number in schedule` inside its `range(current_lap+1,
        # total_laps+1)` loop, so a pit_laps entry <= current_lap or beyond
        # current_lap + remaining_laps never fires, with no error to say so.
        # Rejecting it here surfaces that as a clear 422 instead of a
        # what-if that quietly does nothing. Applied to the top-level plan
        # AND every scenario's own pit_laps (same horizon for all of them —
        # current_lap/remaining_laps are request-level, shared by every
        # scenario in a compare request).
        horizon_end = self.current_lap + self.remaining_laps

        def _out_of_range(pit_laps: list[int]) -> list[int]:
            return [lap for lap in pit_laps if not (self.current_lap < lap <= horizon_end)]

        out_of_range = _out_of_range(self.pit_laps)
        if out_of_range:
            raise ValueError(
                f"pit_laps {out_of_range} must each be greater than current_lap "
                f"({self.current_lap}) and at most current_lap + remaining_laps "
                f"({horizon_end})"
            )

        if self.scenarios:
            for index, scenario in enumerate(self.scenarios):
                scenario_out_of_range = _out_of_range(scenario.pit_laps)
                if scenario_out_of_range:
                    raise ValueError(
                        f"scenarios[{index}].pit_laps {scenario_out_of_range} must each be "
                        f"greater than current_lap ({self.current_lap}) and at most "
                        f"current_lap + remaining_laps ({horizon_end})"
                    )
        return self


class OvertakingDriver(BaseModel):
    """One driver within a pit stop's worth of time of the requester at current_lap.

    driver_id, not driver_code — the frontend resolves id -> code/team color via
    its own driver roster query, same pattern as DriverChip/LiveTimingTower.

    finish_ahead_probability/rival_projected_pit_lap/rival_pit_probability are
    real outputs of the SAME 1000-run Monte Carlo simulate_race call behind
    position_gain_loss/position_probabilities — added for the What-If
    Simulator rebuild part (b) (see
    docs/core-feature-rebuild-whatif-simulator.md §7) so this row's numbers
    can no longer flatly contradict the real simulation result they sit next
    to in the response. All three are None only when the simulation genuinely
    has no data for this rival (should not happen in practice — every rival
    in drivers_overtaken raced in the same simulate_race call — but never
    coerced to a misleading 0.0 default).
    """

    position: int
    driver_id: str
    gap_seconds: float
    # P(the requester finishes ahead of THIS rival specifically), from
    # race_simulator.DriverPositionDistribution.finish_ahead_probability.
    finish_ahead_probability: float | None = None
    # This rival's own single most likely pit lap, from race_simulator.
    # DriverPositionDistribution.projected_pit_laps (that rival's per-lap pit
    # probability across all simulations, summarized to its peak). None means
    # no lap in the simulated remainder had any meaningful pit probability for
    # this rival — not necessarily "never pits," just not concentrated enough
    # to name one lap.
    rival_projected_pit_lap: int | None = None
    # The pit probability AT rival_projected_pit_lap — always present together
    # with rival_projected_pit_lap (both None or both set), so a reader can
    # judge how confident that projection actually is rather than treating the
    # named lap as a certainty.
    rival_pit_probability: float | None = None


class PlanExplanation(BaseModel):
    """Why this plan's position_gain_loss came out the way it did.

    drivers_overtaken is always the same list (drivers behind the requester at
    current_lap, within pit_cost_seconds) regardless of whether the plan's
    result is a gain or a loss — the frontend relabels it depending on
    position_gain_loss's sign ("overtake you" vs "you overtake").
    """

    pit_cost_seconds: float
    drivers_overtaken: list[OvertakingDriver]
    remaining_laps: int
    fresh_tyre_gain_per_lap: float
    total_recoverable_seconds: float


class PositionProbability(BaseModel):
    """One finishing position's probability from the real 1000-run Monte Carlo outcome.

    See race_simulator.DriverPositionDistribution.position_probabilities — a
    dict[int, float] there, reshaped to a list here rather than a JSON
    object keyed by position: a JSON object's keys are always strings, so a
    dict[int, float] field would rely on Pydantic coercing "7" back to 7 on
    the way in — a list of {position, probability} objects has no such
    dependency and is also the more natural shape for a frontend chart to
    iterate over directly.
    """

    position: int
    probability: float


class SimulatedRaceOutcome(BaseModel):
    pit_laps: list[int]
    compounds: list[str]
    # Passed through verbatim from the matching ScenarioPlan.label in a
    # multi-scenario compare request; None for the single-plan path (no
    # scenario to carry a label from — unchanged from before this field
    # existed) or when a scenario itself didn't set one.
    label: str | None = None
    predicted_finish_time: float
    position_gain_loss: int
    # Unrounded mean finishing position across all simulations — position_gain_loss
    # above is round(starting_position - mean_position), which collapses two
    # scenarios differing by e.g. 0.4 positions to the same integer. Exposed
    # separately so a frontend comparison view isn't limited to that rounding.
    mean_position: float
    # Sparse (probability > 0 only) and sorted by position ascending — see
    # PositionProbability's own docstring. The vision this restores:
    # "67% chance of finishing P2, 18% chance P1, 15% chance P3" — computed
    # by race_simulator.simulate_race for every driver on every call already,
    # previously discarded before reaching this schema (see
    # docs/core-feature-rebuild-whatif-simulator.md).
    position_probabilities: list[PositionProbability]
    confidence_interval: tuple[float, float]
    explanation: PlanExplanation


class SimulateStrategyResponse(BaseModel):
    driver_id: uuid.UUID
    # The requesting driver's real track position BEFORE any scenario's laps
    # run — identical across every entry in strategies (one driver, one
    # starting point, N candidate futures), so this lives once at the
    # response's top level rather than duplicated per strategy. Exposed so a
    # frontend can compute P(gain)/P(hold)/P(lose) directly from each
    # strategy's position_probabilities relative to this value, rather than
    # reverse-engineering it from the already-rounded position_gain_loss
    # (which loses precision position_gain_loss's own rounding discards).
    starting_position: int
    strategies: list[SimulatedRaceOutcome]


class SimulateTaskAccepted(BaseModel):
    """202 response for POST /strategy/{session_id}/simulate."""

    task_id: str
    status: str


class SimulateTaskStatusResponse(BaseModel):
    """Response for GET /strategy/simulate/{task_id}, polling the Celery result backend."""

    task_id: str
    status: str
    result: SimulateStrategyResponse | None = None
    # Populated only when status == FAILURE. Deliberately narrow: this route
    # is unauthenticated (see apis/v1/strategy.py's module docstring), so the
    # underlying exception is never echoed verbatim — see get_simulation_result
    # for the F1StrategyError-only safe-message policy this mirrors from
    # core/exceptions.py's unhandled_error_handler.
    error: str | None = None
