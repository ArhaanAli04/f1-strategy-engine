# Core Feature Rebuild — What-If Strategy Simulator

> **Status: ✅ COMPLETE, 2026-09-06 (§1-§6), plus a ✅ COMPLETE follow-on fix,
> 2026-09-07 (§7).** §1-§6's 6 checkpoints (proposed and approved
> checkpoint-by-checkpoint in-session, per §5's own anchor prompt)
> implemented, tested, and verified against real data — see §6 for final
> results per checkpoint. The rest of that part of this document (§1-§5) is
> the original investigation/scoping writeup and is kept as-is for
> historical context — it describes the state of the system *before* this
> rebuild, not the current state; §5's anchor prompt in particular is what
> was actually pasted in to start that session, preserved verbatim rather
> than edited to match what shipped. §7 originally documented one real,
> related bug that session surfaced but deliberately did NOT fix
> (`_build_plan_explanation`'s narrative could contradict the real Monte
> Carlo `position_gain_loss` it explains) — a dedicated 2026-09-07 follow-on
> session (also 6 checkpoints) fixed it fully, both parts; §7 is kept as the
> original problem writeup with a completion summary appended, not rewritten,
> so the "why this was deferred" reasoning stays intact. Both sessions'
> fixes are also tracked in CLAUDE.md's Deferred Wiring/Notes.
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

---

## 6. Completion Summary (2026-09-06)

All 6 checkpoints were implemented, tested, and verified against real data —
checkpoint-by-checkpoint with approval between each. Two corrections to this
document's own §4/§5 that emerged during the session, not deviations from
approval: (1) the anchor prompt's live-progression requirement (§5, mirroring
the pit-window rebuild's §2d) was explicitly waived by the user mid-session —
the Simulator's non-live-race mode simply uses `useLastIngestedSession`
(already built), with no attempt to sync `current_lap` to an active replay's
progression; (2) §4's "further optimization" idea (stacking N scenarios into
one array-batched `simulate_race` call) was explicitly declined in favor of
CP1's per-call memoization alone, once CP1's real-world measurement showed it
was sufficient on its own.

| CP | What | Result |
|---|---|---|
| 1 | Memoize `_tire_deg_predictions` | Deduped on `(tyre_age, driver_id_encoded, compound_encoded)` — the only per-(sim,driver)-varying inputs at a given lap. **~50s → ~7-10s per simulate_race call** on a real ~22-driver field, bit-identical output (not an approximation) — confirmed via a seeded test compared against the un-deduped path. This is what made server-orchestrated multi-scenario comparison viable at all; without it, 3 scenarios would cost ~150s, past `useSimulationResult`'s 60s client timeout. |
| 2 | Expose `position_probabilities`/`mean_position` | New `PositionProbability` (`{position, probability}`, a list — not a `dict[int, float]`, to avoid depending on Pydantic's JSON-string-key coercion) on `SimulatedRaceOutcome`, sparse and sorted ascending. Restores the vision's own "67% chance of finishing P2, 18% chance P1, 15% chance P3" — computed by `race_simulator.simulate_race` on every call already, previously discarded (§2c/§3's central finding). Single-scenario response shape only, no multi-scenario change yet. |
| 3 | Multi-scenario request/response (server-orchestrated) | `SimulateStrategyRequest.scenarios` (1-4 `ScenarioPlan`s, mutually exclusive with the existing top-level `pit_laps`/`compounds`, each independently horizon-validated) — one `_build_race_state` call, N `race_simulator.simulate_race` calls sharing ONE random seed (`secrets.randbelow`) across all N ("common random numbers" — isolates the comparison to each scenario's own pit-lap decision, not independently-drawn safety-car/noise randomness; verified via two identical scenarios producing bit-identical Monte Carlo output in a real integration test). Also added top-level `SimulateStrategyResponse.starting_position` so the frontend can compute P(Gain)/P(Hold)/P(Lose) without reverse-engineering it from the already-rounded `position_gain_loss`. Closes §3's first gap row — server-orchestrated was chosen over client-orchestrated (§4's option (a)) because polling N tasks at 2s intervals would exceed the 60/minute authenticated rate limit, and `_build_race_state`'s DB queries would otherwise repeat N times for identical data. |
| 4 | Frontend: compare mode + distribution chart | New `PositionDistributionChart.tsx` (`components/strategy/`) — a grouped bar chart (x=finishing position, one series per scenario) plus a risk/reward table (Mean Position, P(Gain)/P(Hold)/P(Lose), Finish Time Range), using the dataviz skill's validated 4-slot dark categorical palette (blue/orange/aqua/yellow — worst adjacent CVD ΔE 8.4, normal-vision ΔE 19.8, all >=3:1 contrast against this app's actual `--card` surface). `SimulatorPage.tsx` gained a Single Plan/Compare Scenarios mode toggle — deliberately kept separate mental models (§4's own open question, resolved): Compare mode is always a single pit lap per scenario (matching the vision's literal "lap 30 vs 33 vs 36" example), never a multi-stop sequence; the existing "+ Add Pit Stop" sequential planner is untouched. Closes §3's remaining two gap rows. |
| 5 | Desktop/mobile port | Desktop: full port (verbatim `PositionDistributionChart.tsx` copy — zero web-specific dependencies — plus an adapted `SimulatorPage.tsx`, CSV export extended with the new columns). Mobile: data layer only (`types/simulate.ts` synced, `PlanExplanationCard.tsx` got the CP6/§7 wording fix) — the mode toggle and a native equivalent of the distribution chart were deliberately scoped OUT as a dedicated future effort (mobile's existing chart already needs a synthetic-series workaround for simple gain/loss coloring; a *dynamically-sized* grouped bar chart is a bigger, separate native-charting task), disclosed inline in `mobile/src/README.md`. |
| 6 | Docs | This completion summary; CLAUDE.md's Notes and Deferred Wiring sections updated to match. |

**Key validation** — a genuine 3-scenario compare request against the real
running stack (not a mock), Belgian GP 2026 R10, pit laps 25/28/31 for the
same driver/race-state: **resolved in 34s total** (comfortably under the 60s
client timeout), each scenario producing genuinely different, plausible
outcomes —

```
label='Pit lap 25'  pit_laps=[25]  mean_pos=6.826  finish=5093.56  top: P7 80.1%, P6 18.7%
label='Pit lap 28'  pit_laps=[28]  mean_pos=6.545  finish=5091.83  top: P7 54.7%, P6 44.5%
label='Pit lap 31'  pit_laps=[31]  mean_pos=6.113  finish=5089.53  top: P6 74.8%, P7 18.8%
```

— exactly the vision's own use case (§1), now real.

**Supplementary validation — a genuine LIVE race, not historical/replay
(2026-09-06, Italian GP 2026 Round 13, Monza):** every prior verification in
this document used a completed, historically-ingested session
(`ingest_historical.py`). This one ran while the race was actually live via
`ingest_live_session.py`'s real live path — different data-freshness
characteristics (only 3 laps ingested at query time), different validation
edge (`current_lap` one past real progress, the genuine "what-if starting
now" case `strategy_service.validate_current_lap` exists for), and it held
up. The user ran a real 2-scenario compare for VER at lap 4 (pit lap 30 on
HARD vs. pit lap 23 on MEDIUM, 53 remaining-lap horizon — Monza's real race
distance). Independently cross-checked against the live database, not just
"the numbers look plausible":
- `starting_position` (back-calculated from the returned
  `position_gain_loss`/`mean_position` pair, consistent across both
  scenarios) = 4 — matches VER's real `lap_data.position` at lap 3 (their
  latest actually-ingested lap) exactly.
- Every one of the 9 `drivers_overtaken` entries' `gap_seconds` was
  recomputed from the DB's own `SUM(lap_time_seconds)` through lap 3 (this
  session has no `session_elapsed_seconds` — never populated for a live
  session, only backfilled historical ones, per CLAUDE.md's Deferred
  Wiring) and matched the API's reported value to 0.1s on all 9 (COL 0.9s,
  PIA 8.0s, NOR 9.6s, LIN 10.8s, HAM 12.1s, BEA 17.3s, OCO 18.5s, ANT 19.9s,
  BOR 20.6s), in the correct gap-ascending order.
- The Option 3 wording fix (§7) rendered correctly in production — the
  exact intended sentence ("This is a simplified snapshot that assumes
  rivals hold their current pace... the Monte Carlo position change above
  already accounts for rivals' own tyre wear and pit stops") appeared under
  both scenarios' explanation cards.

This is decisive evidence the feature is a faithful reflection of the real
live database state, not something that only happens to look right against
one already-tested historical session.

Test coverage added:
8 new schema-validation unit tests, a `_tire_deg_predictions` memoization
correctness test (seeded, bit-for-bit vs. the un-deduped path), 2 new
integration tests (shared-seed correctness via two identical scenarios;
single-plan-path regression guard), 7 new `PositionDistributionChart` unit
tests (position-union/zero-fill, label fallback, legend gating, P/gain-hold-
lose math), 5 new `SimulatorPage` compare-mode tests (mode toggle, payload
shape, scenario cap, minimum-1-scenario guard). Full suites green: 302
backend unit + 3 relevant integration tests; 54 web vitest tests; `tsc`
clean on web/desktop/mobile; full production `vite build` succeeds on
web/desktop.

**Open follow-ups (not blocking — see CLAUDE.md's Deferred Wiring &
Integration Gaps):**
1. §7 below — `_build_plan_explanation`'s narrative can still contradict
   the real Monte Carlo number it explains; only a cheap wording mitigation
   shipped this session, not the full fix.
2. Mobile's Compare Scenarios UI + native distribution chart (CP5's own
   scoped-out item).
3. `_run_one_scenario`'s shared-seed idea (CP3) is not applied to the
   single-plan path, by design — that path still uses an unseeded RNG,
   unchanged from before this rebuild.

---

## 7. Deferred — `_build_plan_explanation`'s narrative can contradict the real simulation

**Not fixed this session — needs a dedicated future session.** Discovered
2026-09-06 during manual verification of Checkpoint 4's Compare Scenarios
mode: the user ran two real scenarios (pit lap 25 vs. pit lap 31) and got
back a `PlanExplanationCard` for each reading, almost verbatim, "Only 19
[13] laps remaining after pit — not enough to recover on fresh tyres... Fresh
HARD tyre advantage: ~0.3s/lap — recovers only ~5.7s [3.9s] in 19 [13]
laps," listing the SAME 5 rivals (HAM, NOR, PIA, ANT, VER) as having
overtaken and never being caught, for BOTH scenarios. The user correctly
identified this as unrealistic: those rivals will also need to pit
eventually (or suffer catastrophic tyre wear staying out), so treating them
as a permanent, un-catchable wall is wrong — and the repetition across two
different pit laps, side by side in the new Compare view, is what made the
flaw obvious in a way a single-scenario view never had.

**Root cause, confirmed by code trace, not assumption:**
`prediction_worker._build_plan_explanation` is a static heuristic, entirely
disconnected from the real Monte Carlo simulation two lines above it in the
same response:
- `drivers_overtaken` is a snapshot of the field's gaps AT `current_lap` —
  frozen. Reasonable on its own terms ("who's close enough to leapfrog you
  right now"), but not evolved forward at all.
- `fresh_tyre_gain_per_lap` comes from `_FRESH_TYRE_GAIN_PER_LAP_SECONDS`,
  a **hardcoded per-compound constant** (`{"HARD": 0.3, "MEDIUM": 0.5,
  "SOFT": 0.8}`) — not derived from the tire_deg model's actual predicted
  degradation curve at all.
- `total_recoverable_seconds = fresh_tyre_gain_per_lap × laps_after_pit`
  implicitly assumes every rival in `drivers_overtaken` holds their EXACT
  current pace for the rest of the race, tyres never degrading, never
  pitting again.

This is a real, confirmed divergence from the actual simulation:
`race_simulator.simulate_race`'s per-lap loop gives every driver EXCEPT the
requester the pit_predictor model's own autonomous pit decision, every lap,
for the whole simulated remainder — `forced_pit_laps` only ever overrides
the requester's own flag. Every rival's tyre degradation is modelled too
(the same batched `_tire_deg_predictions` call, using their own evolving
compound/tyre age). So `position_gain_loss`/`mean_position` already account
for rivals eventually pitting and losing time — the narrative explaining
that number does not, and can flatly contradict it.

**Mitigated 2026-09-06 (Option 3 — cheap, honest, shipped to all 3
clients):** removed the "sufficient"/"not enough to recover" verdict
language entirely from `PlanExplanationCard` (web/desktop's inline copy in
`SimulatorPage.tsx`; mobile's `components/strategy/PlanExplanationCard.tsx`)
— the remaining sentence states the assumption explicitly ("this is a
simplified snapshot that assumes rivals hold their current pace with no
further pit stops of their own — the Monte Carlo position change above
already accounts for rivals' own tyre wear and pit stops") instead of
asserting a conclusion the real number can contradict. This is a copy-only
fix — no model or data changes, and it does not make the narrative a
genuine explanation of the real number, only stops it from actively
contradicting one.

**Full fix — NOT done, two parts, real architectural work:**
1. **Replace the hardcoded constants with a real tire_deg model call.**
   Predict the OLD compound's degradation delta at its current/growing tyre
   age vs. the NEW compound at `tyre_age=0`, for the requester specifically
   — removes the "magic number," still doesn't model rivals pitting.
   Smaller, contained change (~30-60 min).
2. **Extract real per-lap pit events for every rival from the actual
   simulation run, and build the narrative from that.** This is the real
   fix. `race_simulator.simulate_race`/`RaceSimulationResult` currently
   returns only FINAL AGGREGATED distributions per driver
   (`DriverPositionDistribution`) — no per-lap history survives the call at
   all. Needs a new return shape (e.g. per-driver per-lap `pit_flags`/
   compound/position across some or all of the 1000 sims, or a
   representative summary) threaded through `_run_one_scenario`/
   `_build_plan_explanation`. Real engineering effort — likely several
   hours on its own, not a quick follow-on to part 1.

**Why this matters:** the narrative sits directly below the real
`position_gain_loss` number in the same card, so a reader has every reason
to assume it's explaining that number — the Option 3 mitigation stops it
from asserting things that flatly contradict the real number, but doesn't
make it a genuine, dynamically-correct explanation. Parts 1+2 above would
close that gap for real. Held for a dedicated future session per explicit
user decision 2026-09-06 — do not fold into an unrelated change.

---

## §7 Completion Summary (2026-09-07)

**Both parts above are done.** A dedicated follow-on session (independently
investigated and confirmed this section's own findings against the current
codebase first, per its own anchor-prompt-style discipline) implemented and
verified both, checkpoint-by-checkpoint with approval between each — same
convention as §5/§6 above.

| CP | What | Result |
|---|---|---|
| 1 | Part 1: real tire_deg-derived degradation | New shared `tire_deg_model.project_stint_delta` (lifts `strategy_service._project_stint_delta`'s logic, adding the `pipeline_feature_count` schema guard `race_simulator._tire_deg_predictions` already uses elsewhere) — `strategy_service._project_stint_delta` now delegates to it, one implementation instead of two. `_build_plan_explanation` projects the OLD compound continuing to degrade (real tyre age at the plan's last forced pit) vs. the NEW compound fresh at `tyre_age=0`, both over the laps remaining after that pit. **Caught a real bug not in the original plan:** a multi-stop plan's LAST pit must resolve "old compound"/tyre-age from the PREVIOUS forced stop (`compounds[-2]`/`pit_laps[-2]`), not the plan's STARTING compound — the original scoping assumed a single pit stop implicitly. Falls back to the original hardcoded constant (non-regressive) when a real projection can't be made. The value can now be genuinely NEGATIVE (new compound projected slower — e.g. dry-track INTERMEDIATE, see CLAUDE.md's track-condition-input limitation) — a real signal the old constant could never produce. Also incidentally fixed: INTERMEDIATE/WET had no entry in the old constant dict at all (silently rendered nothing), now get a real value like every other compound. |
| 2 | Frontend: sign-aware gate + copy | All 3 clients' render gate changed from `fresh_tyre_gain_per_lap > 0` to `remaining_laps > 0` (the sign is no longer what decides whether to show the line) and the copy rewritten to a `isFasterOnFreshTyre`-branched sentence — "X s/lap faster... recovering ~Ys" vs. "X s/lap SLOWER... this pit adds ~Ys". |
| 3 | Part 2: `race_simulator` surfaces real per-driver simulation data | `DriverPositionDistribution` gained `projected_pit_laps` (a driver's own per-lap pit probability across all sims, captured from the SAME `pit_flags` array that already fires each simulated pit stop — captured AFTER the forced-pit override, so a what-if's forced lap correctly shows `1.0`) and `finish_ahead_probability` (P(this driver finishes ahead of each other driver), from the same final `cumulative_time` array `position_probabilities` is already built from). Both additive dataclass fields with empty defaults — no existing call site needed updating. **Caught a real test-tolerance bug, not a code bug:** an SC lap on the simulated FINAL lap can bunch two drivers to an identical value, a genuine exact tie that makes the two directions' `finish_ahead_probability` sum to just under 1.0 by design — the first version of this checkpoint's own test asserted exact 1.0 and failed; fixed by isolating the property with a zero-probability SC mock rather than loosening the tolerance to paper over an unexplained number. |
| 4 | `_build_plan_explanation` consumes it | `OvertakingDriver` gained 3 nullable fields — the requester's real `finish_ahead_probability` for that specific rival, and that rival's own peak `projected_pit_laps` entry (lap + probability, via a new `_peak_projected_pit_lap` helper; ties resolve to the earliest lap). The list's SELECTION criterion (who appears in `drivers_overtaken`) is deliberately unchanged — this checkpoint enriches each row's DATA only, per this document's own §7 scope decision above. `driver_distributions_by_id` (built once per scenario from `result.driver_distributions`) threads through `_run_one_scenario`. |
| 5 | Frontend render (3 clients) | Each `drivers_overtaken` row gained a second, compact muted line — e.g. "62% chance you finish ahead · pits ~lap 34 (71%)" — via a `formatOvertakingEnrichment` helper, rendering only whichever piece(s) are non-null (never a fabricated placeholder). |
| 6 | Verification + docs | This completion summary; CLAUDE.md's Deferred Wiring entry updated to `[✅ done]` in place, plus one new deferred item (below). |

**Verified:** 27 new/updated backend unit tests (`tire_deg_model.
project_stint_delta`, `prediction_worker`'s degradation projection and
`drivers_overtaken` enrichment, `race_simulator`'s new fields). Full backend
unit suite 324 passed; integration suite (`test_race_simulation_
serialization`/`test_strategy_endpoint`/`test_live_prediction_pipeline`) 14
passed; `tsc`/`oxlint` clean on web/desktop/mobile; production `vite build`
succeeds on web/desktop.

**End-to-end against the real running stack** (Belgian GP 2026 R10 via
`GET /strategy/last-ingested-session` — no live race was ingested/testable
at verification time; Demo Replay was explicitly OUT of scope per this
session's own corrected instruction — the Simulator was already
deliberately scoped, in the original §1-§6 rebuild, to not sync with an
active Demo Replay, so replay-mode verification was never required here):
a real what-if for NOR (pit lap 30 onto HARD, Belgian GP R10) returned 5
real `drivers_overtaken` rows, each with a real `finish_ahead_probability`
— cross-checked directly: the one rival showing `0.976` was indeed the only
rival the response's own `position_probabilities` showed the requester
finishing immediately ahead of in the vast majority of simulations, the
other 4 (all ahead of the requester's own likely finishing position) showed
`0.0`. `rival_projected_pit_lap`/`rival_pit_probability` came back `null`
for every non-forced rival tried across several real lap windows, including
one built specifically around a real driver's (COL's) own actual lap-16 pit
stop in this session — investigated rather than dismissed (see the new
Deferred Wiring item below) and confirmed to be a genuine, pre-existing
`tire_deg_model` characteristic unrelated to this fix's own code, not a
wiring defect: a forced pit lap in the same verification session correctly
showed probability exactly `1.0` at the forced lap, proving the mechanism
itself works.

**One real environment gotcha hit and fixed during verification, not a code
bug:** the `backend` container's `uvicorn --reload` had not picked up the
`simulate_schema.py` change (no reload logged) despite being up for over an
hour after the edit, so the first live verification attempt got back
`drivers_overtaken` rows with all 3 new fields silently absent (Pydantic
dropping unrecognized dict keys, not an error — the WORKER container,
already restarted per this project's own documented Celery convention, was
computing and returning them correctly). Resolved with an explicit
`docker compose restart backend`. Worth remembering for any future
verification: a schema-only change needs the backend container restarted
too, not just the worker, if `--reload` doesn't visibly log a reload.

**New deferred item this verification surfaced** (added to CLAUDE.md's
Deferred Wiring, same class as the existing `tire_deg_hard.pkl` first-lap
entry — real ML work, not attempted): `tire_deg_model.
predict_life_remaining_batch` returns `MAX_LOOKAHEAD_LAPS` (40) for
SOFT/HARD/WET across almost the entire realistic tyre-age range (and past
`tyre_age_laps≈2` on MEDIUM) for this exact promoted model set — confirmed
via a direct sweep of each pipeline's raw predictions, not assumed. Since
`predicted_life_remaining` feeds `pit_predictor`'s feature vector, this
makes `race_simulator`'s organic (non-forced) pit decisions — and therefore
`projected_pit_laps` — read as near-permanently "don't pit" in a
short-horizon forward replay of already-past historical data, regardless of
real gap/position context. Does not reduce the value of this session's own
fix: `finish_ahead_probability` (the half of the enrichment actually
exercised in every real scenario tried) is unaffected, and a `null` pit
projection is the field's own documented, correctly-handled "no signal"
contract — but it does mean `rival_projected_pit_lap` will likely stay
`null` in most real usage until that underlying model characteristic is
addressed.
