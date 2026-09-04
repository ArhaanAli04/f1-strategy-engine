# Core Feature Rebuild — What-If Strategy Simulator

> **Status:** Investigation only, 2026-09-04. Nothing in this document has
> been implemented or fixed. This is a scoping document for a future
> dedicated session (or several) to close the gap between this project's
> originally planned What-If Strategy Simulator and what currently exists —
> a real, working single-scenario Monte Carlo simulator whose richest output
> (a full per-position probability distribution) is already computed
> internally and then thrown away before it ever reaches the API or the UI.
>
> Produced as a follow-on to the core-feature rebuild session that closed
> `docs/core-feature-rebuild-strategy-recommendations.md` (the pit-window
> recommendation engine) — same investigate-honestly-before-touching-code
> discipline, applied to the project's third originally-planned core
> feature.

---

## 1. Original Vision

From early project planning, verbatim:

> **3. What-If Strategy Simulator (Interactive)**
>
> What the user sees: An interactive screen where they can input:
> - "My driver: Norris, currently lap 28, MEDIUM tyres, age 18 laps, P2"
> - "Scenario: What if he pits on lap 30 vs lap 33 vs lap 36?"
>
> The system runs 1000 Monte Carlo simulations for each scenario and
> returns:
> - "Pit lap 30: 67% chance of finishing P2, 18% chance P1, 15% chance P3"
> - "Pit lap 33: 71% chance P2, 12% chance P1, 17% chance P3"
> - "Pit lap 36: 54% chance P2, 8% chance P1, 38% chance P3-4"
>
> The user can see visually why lap 33 is optimal — the position
> probability distribution chart shows the risk/reward profile of each
> decision.

Three distinct elements to hold the current system against:

1. **Side-by-side comparison of multiple candidate pit laps** in one
   interaction ("lap 30 vs 33 vs 36"), not one scenario at a time.
2. **A full finishing-position probability distribution per scenario**
   (P1/P2/P3/… percentages), not a single number.
3. **A visual chart of that distribution**, so the risk/reward profile of
   each candidate is comparable at a glance.

---

## 2. What Currently Exists (honest inventory)

A single-scenario Monte Carlo simulator that is real, already computes
everything the vision needs internally, and discards the one output the
vision is actually built around before it ever leaves the backend.

### 2a. One request = one scenario, always

`SimulatorPage.tsx`'s wizard (`web/src/pages/SimulatorPage.tsx`) collects
one driver, one current race state, and one list of pit stops (`pitStops:
PitStopRow[]`, "+ Add Pit Stop") into a single `SimulateStrategyRequest`
(`pit_laps: number[]`, `compounds: string[]`) and calls
`useSimulateStrategy` **once** per "Run Simulation" click.

Backend confirms this is a **sequential multi-stop plan for one race**, not
N independent alternatives: `SimulateStrategyRequest.pit_laps`/`compounds`
are parallel arrays consumed by `prediction_worker._run_simulation` as a
single `forced_pit_laps: dict[driver_id, dict[lap_number, (compound,
encoded)]]` schedule, passed to `race_simulator.simulate_race` **once**.
Adding rows in the UI means "pit at lap 15 on HARD, then also pit at lap 33
on SOFT, in the same simulated race" — not "compare pitting at lap 15 vs.
pitting at lap 33." There is no mechanism anywhere in the request schema,
the worker, or the frontend that submits several candidate single-pit-lap
scenarios and returns them together. `SimulateStrategyResponse.strategies`
is a list (plural) structurally, but `_run_simulation`'s return value is
always `"strategies": [ {...one dict...} ]` — confirmed by reading the
literal return statement, not inferred. A user wanting "lap 30 vs 33 vs 36"
today would have to run the wizard three separate times and manually
remember/compare the results themselves — `handleReset` clears all state
between runs, so not even that manual comparison is supported by the UI.

### 2b. The response has no position probability distribution — a single rounded number instead

`SimulatedRaceOutcome` (`backend/schemas/simulate_schema.py`):

```python
class SimulatedRaceOutcome(BaseModel):
    pit_laps: list[int]
    compounds: list[str]
    predicted_finish_time: float
    position_gain_loss: int
    confidence_interval: tuple[float, float]
    explanation: PlanExplanation
```

`position_gain_loss` is computed in `_run_simulation` as:

```python
position_gain_loss = round(requester_state.starting_position - requesting_distribution.mean_position)
```

— a single rounded integer (mean position change across all 1000
simulations), not a distribution. `confidence_interval` is the 5th/95th
percentile of **finish time in seconds** (`finish_time_p5_seconds`,
`finish_time_p95_seconds`), not a position-percentage range. Nothing in the
response resembles "67% chance of finishing P2, 18% chance P1, 15% chance
P3" — the vision's own example shape does not exist in this schema at all.

### 2c. `race_simulator.py` already computes the exact thing the vision wants — internally, then discards it

This is the most important finding. `race_simulator.simulate_race` returns:

```python
@dataclass(frozen=True)
class DriverPositionDistribution:
    driver_id: str
    position_probabilities: dict[int, float]   # <- exactly the vision's ask
    mean_position: float
    mean_finish_time_seconds: float
    finish_time_p5_seconds: float
    finish_time_p95_seconds: float

@dataclass(frozen=True)
class RaceSimulationResult:
    n_simulations: int
    driver_distributions: list[DriverPositionDistribution]
```

`position_probabilities` is built directly from the real 1000-simulation
outcome array (`race_simulator.py` lines ~584-601):

```python
order = np.argsort(cumulative_time, axis=1)
finishing_positions = np.argsort(order, axis=1) + 1
...
counts = np.bincount(finishing_positions[:, i], minlength=n_drivers + 1)[1 : n_drivers + 1]
probabilities = counts / n_simulations
...
position_probabilities={p + 1: float(probabilities[p]) for p in range(n_drivers)}
```

This is a genuine, already-computed finishing-position probability
distribution — for **every driver in the field**, not just the requester —
produced by exactly the same 1000-run Monte Carlo loop the vision describes
("The system runs 1000 Monte Carlo simulations"). `N_SIMULATIONS = 1000` is
already the module's own default constant.

`prediction_worker._run_simulation` receives this full result
(`requesting_distribution = next(d for d in result.driver_distributions if
d.driver_id == requester_id_str)`) and reads exactly three scalar fields off
it — `mean_position`, `mean_finish_time_seconds`,
`finish_time_p5_seconds`/`finish_time_p95_seconds` — never
`position_probabilities`. The dict is computed, held in memory, and then
goes out of scope unread. No serialization gap on the numerics side exists
either: `dict[int, float]` round-trips through Pydantic/JSON/Celery's result
backend without any special handling (confirmed by the pit-window rebuild's
own `test_race_simulation_serialization.py` precedent — a `tuple` already
round-trips cleanly through the identical path `confidence_interval` uses;
a `dict[int, float]` is no harder).

### 2d. The frontend has no distribution chart — only a position-change bar chart and a text explanation

Step 4 of `SimulatorPage.tsx` renders, per strategy:
- A `recharts` `BarChart` of `position_gain_loss` (one bar per strategy —
  currently always exactly one bar per run, per §2a).
- A text list including `confidence_interval` as a **finish-time range**
  ("Finish Time Range" column).
- `PlanExplanationCard` — `drivers_overtaken`/`drivers_lost_to` narrative
  text (pit-cost-seconds framing), not a probability breakdown.

No component anywhere in `web/src/components/strategy/` or
`web/src/pages/SimulatorPage.tsx` renders a per-position percentage
breakdown or a stacked/grouped bar chart across P1/P2/P3/… — searched for
"scenario"/"compare"/"position_probabilit" across `web/src` and found no
matches relevant to this feature (a few unrelated hits: a code comment
using the word "scenario," and `SectorComparison.tsx`, which compares lap
sectors, not simulator outcomes).

---

## 3. Gap Analysis

| Vision element | Currently exists? | What's missing/broken |
|---|---|---|
| Compare multiple candidate pit laps in one interaction ("lap 30 vs 33 vs 36") | ❌ | One request = one sequential multi-stop plan for one race, not N independent alternatives. No schema, worker, or UI mechanism submits/returns several scenarios together. `strategies` is a list in name only — always length 1 in practice. |
| Full finishing-position probability distribution per scenario ("67% P2, 18% P1, 15% P3") | ⚠️ computed, not exposed | `race_simulator.simulate_race` already computes exactly this (`DriverPositionDistribution.position_probabilities`, real 1000-run Monte Carlo `np.bincount`) for every driver — `prediction_worker._run_simulation` reads it, extracts only `mean_position`/finish-time percentiles from it, and discards the dict. Not in `SimulatedRaceOutcome`, not in the API response, not in the frontend type. |
| "1000 Monte Carlo simulations" | ✅ | `N_SIMULATIONS = 1000` is already the real default, already exercised on every simulate call. |
| Visual position-probability-distribution chart ("risk/reward profile") | ❌ | Step 4 only charts `position_gain_loss` (a single number) per strategy. No stacked/grouped bar or equivalent chart of P1/P2/P3/… percentages exists anywhere in the frontend. |
| Multi-factor explanation of the outcome | ✅ (different framing) | `PlanExplanation`/`PlanExplanationCard` already give a real, evidence-based narrative (pit cost, drivers overtaken, fresh-tyre recovery) — not what the vision's example shows, but a working, arguably richer mechanism already exists from the pit-window rebuild. Likely reusable as-is alongside a distribution chart, not something this gap analysis flags as broken. |
| Interactive input (driver, lap, tyres, age, position) | ✅ | `SimulatorPage.tsx`'s step 1/2 wizard already collects all of this, auto-filled from the driver's latest real lap when available. |

---

## 4. What Needs To Change

High-level only — this is scoping, not an implementation plan. The future
session should design the actual approach (see §5).

- **Expose `position_probabilities` in the API response.** The hardest
  part (computing it) is already done and already correct — this is
  primarily a schema + wiring change: add a `position_probabilities:
  dict[int, float]` (or a list of `{position, probability}` entries, a
  JSON-object-with-int-keys design question for the future session) field
  to `SimulatedRaceOutcome`, and stop discarding
  `requesting_distribution.position_probabilities` in
  `prediction_worker._run_simulation`.
- **Decide how multi-scenario comparison should work, architecturally.**
  Two real options, not adjudicated here:
  (a) **Client-orchestrated:** the frontend fires 3 separate `POST
  /simulate` calls (one per candidate pit lap) and merges the 3 polled
  results into one comparison view. Needs no backend schema change beyond
  the `position_probabilities` field above; the "run 3 scenarios" concept
  lives entirely in the frontend (e.g. a "compare pit laps" step 2 variant
  that lets the user list 3 candidate laps instead of building one
  sequential multi-stop plan, firing one request per candidate).
  (b) **Server-orchestrated:** a new request/response shape (or an
  extension of the existing one) that accepts a list of candidate
  single-pit-lap scenarios and runs `race_simulator.simulate_race` once per
  candidate inside one Celery task, returning N `SimulatedRaceOutcome`
  entries in one `strategies` array — which the schema's existing list
  shape already supports structurally. Fewer round trips and matches the
  vision's "the system runs 1000 Monte Carlo simulations for each
  scenario" framing more literally, but means 3x the per-request compute
  cost inside a single task (each `simulate_race` call is already a full
  remaining-race-length loop of batched ML inference across
  `N_SIMULATIONS`) — a real performance question for the future session to
  size, not resolved here.
- **A position-probability-distribution chart needs to be built.**
  Whatever shape §4's first bullet lands on, the frontend needs a new
  visualization (e.g. a grouped/stacked bar chart, one group per scenario,
  bars for P1/P2/P3/…) — nothing today renders this. `recharts` is already
  a project dependency and already used for the existing bar chart, so this
  is additive within the existing toolchain, not a new library.
- **Decide what "comparison" means for a multi-stop plan vs. a single
  candidate lap.** The vision's own example ("pit on lap 30 vs 33 vs 36")
  is inherently about comparing single-decision alternatives for one pit
  stop, not comparing different multi-stop strategies. The existing
  "+ Add Pit Stop" UI (multiple sequential stops in one plan) and a new
  "compare these candidate laps" UI serve genuinely different user intents
  — the future session should decide whether both coexist, and if so how
  they're presented without confusing the two mental models.
- **Must work identically in LIVE and REPLAY mode, verified against
  both, not just one.** Same requirement as the pit-window rebuild (see
  that document's §2d/§4): `race_simulator.simulate_race` and
  `prediction_worker._run_simulation` are already shared, request-driven
  code (not part of the per-lap live/replay dispatch pipeline itself), so
  this is lower-risk than the pit-window rebuild's CP1 was — but the
  Simulator page's own session/driver auto-fill behavior already branches
  on live-vs-replay (`isLiveSessionMode`, see `SimulatorPage.tsx`), and any
  new comparison UI must be verified to behave correctly in both.

---

## 5. Anchor Prompt — paste into a new session

```
Read docs/core-feature-rebuild-whatif-simulator.md in full before doing
anything else. It documents a real, evidence-based gap between this
project's originally-planned What-If Strategy Simulator (side-by-side
comparison of multiple candidate pit laps, each with a full finishing-
position probability distribution and a risk/reward chart) and what
currently exists (a real, working single-scenario Monte Carlo simulator
whose richest output — a genuine per-position probability distribution,
already computed from 1000 real simulations — is silently discarded before
it ever reaches the API or the frontend).

Do NOT trust this document's findings blindly — independently verify
everything in it against the current codebase before proposing anything.
It was written 2026-09-04, immediately after the core-feature-rebuild
session that closed docs/core-feature-rebuild-strategy-recommendations.md
(the pit-window recommendation engine) — re-read CLAUDE.md's Deferred
Wiring and Notes sections fresh, and re-check the actual current state of
every file this document cites (backend/services/ml/race_simulator.py,
backend/workers/prediction_worker.py, backend/schemas/simulate_schema.py,
backend/apis/v1/strategy.py, web/src/pages/SimulatorPage.tsx, web/src/
types/simulate.ts, web/src/hooks/useStrategy.ts) rather than assuming this
document's line-level claims still hold.

This is a significant feature addition, not a quick fix — likely multiple
checkpoints, quite possibly multiple sessions. Do not jump to
implementation. Before writing any code:

1. Re-investigate the current state of the Simulator (§2 of the document)
   and confirm or correct this document's Gap Analysis (§3) against what
   you find.
2. Propose a complete end-to-end plan for what the rebuilt feature should
   look like — whether multi-scenario comparison is client-orchestrated
   (N separate requests, merged in the frontend) or server-orchestrated (a
   new request/response shape running N simulations in one task) — see
   §4's two options, not pre-decided; how `position_probabilities` should
   be shaped in the API response; and how the frontend should visualize a
   risk/reward distribution chart across scenarios.
3. Present that plan and wait for approval before implementing anything —
   same checkpoint-based convention used throughout this project's other
   deferred-item and rebuild sessions (propose, get approval, implement
   checkpoint by checkpoint, report + wait between each). Expect this to
   span multiple checkpoints.

Whatever solution you propose and build MUST work correctly in BOTH of this
project's live-progression scenarios — a genuinely live race
(ingest_live_session.py) and Demo Replay (replay_pipeline.py) — same
requirement as the pit-window rebuild's own §2d. The Simulator page's
session/driver auto-fill already branches on live-vs-replay
(isLiveSessionMode in SimulatorPage.tsx); verify any new comparison UI
behaves correctly in both before considering any part of this work
complete.

Do not run git commands unless explicitly asked.
```
