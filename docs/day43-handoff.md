# Day 43 Handoff — Demo Replay (Checkpoints A-F + Part 2 complete)

Written at the end of the Day 43 session. Read this in full before resuming
— it's the single source of truth for exactly where things stand.
Checkpoints A-F (that session's actual scope) are done and verified.

**Part 2 (spec Part 3.2/4/5/6 — CLI safety guard, replay-control API +
kill-switch, frontend selector UI, safety testing) is now also complete —
see section 8 below.** The rest of this document (sections 1-7) is the
original A-F handoff, kept for context; the "not started" language in
sections 1-2 refers to the state *before* Part 2 and is superseded by
section 8.

## 1. What's done — Checkpoints A-F

### Checkpoint A — Curated session ingestion
Ingested Belgian GP (2026 Round 10, `make ingest SEASON=2026 ROUND=10
SESSION_TYPE=R`) and Canadian GP (2026 Round 5) alongside the
already-ingested British GP (2026 Round 9), giving 3 curated sessions with
justified 10-lap windows. See section 3 for exact session_ids/lap ranges.

### Checkpoint B — Position data
- New `DriverPosition` model (`backend/models/telemetry.py`):
  `id, session_id, driver_id, lap_number, timestamp_in_lap, x, y`.
- Migration `9cd0463cbe60_add_driver_positions_table` — applied.
- New `backend/scripts/ingest_position_data.py` — loads a session with
  `telemetry=True`, downsamples FastF1's native ~3-4Hz `Lap.get_pos_data()`
  to 1Hz, delete-then-reinserts per (session, lap range) for idempotent
  re-runs. Run for all 3 curated sessions: ~62K rows total.

### Checkpoint C — `replay_pipeline.py` core extensions
Started as three additions (car-number resolution, targeted gap
computation, per-lap position playback) and went through **three real
rounds of fixes**, all found via your manual testing, not caught in
initial verification:

1. **Position collapsing real gaps** — `driver_positions.timestamp_in_lap`
   is relative to each driver's own lap start; the first implementation
   grouped/published purely by that relative value, so two drivers 20-53s
   apart in real time both appeared "0 seconds into lap 43" simultaneously
   — the field looked clustered on every lap, not just near the Safety
   Car. Root-caused with real FastF1 data (LEC vs. HAD's real 53.2s gap at
   lap 43) and fixed by reconstructing each sample's true absolute session
   time (`LapStartTime.total_seconds() + timestamp_in_lap`, using FastF1's
   already-loaded per-lap `LapStartTime`) and streaming the whole curated
   window on one shared real-time clock.
2. **Disappearing dots at lap boundaries** — the per-lap-burst structure
   only published positions during each lap's own ~90-160s burst, then
   went silent for the rest of that lap's `--rate`-paced driver-dispatch
   phase; the 3s position-key TTL meant dots visibly expired in the gap.
   Fixed by the same redesign above: one continuous background thread
   (`_run_position_timeline`, started via `threading.Thread`) streaming
   positions for the whole window, decoupled from `--rate` entirely.
3. **`--start-lap`/`--end-lap` not scoping position playback** — these
   flags only restricted the lap-completion dispatch loop; a 2-lap-scoped
   test still played back the *entire* 10-lap window (~1242s instead of
   the expected ~400s). Fixed in `_fetch_all_positions`.
4. **Gaps/position desync, confirmed up to -249s drift** — gaps were
   recomputed once per lap-transition in the `--rate`-paced dispatch loop
   (pace = `rate_seconds × drivers_in_lap`), while positions ran on true
   real time. Quantified with real FastF1 lap durations vs. the `fast`/5s
   preset's dispatch pace: laps 43-47 drifted dispatch +7s→+39s ahead;
   laps 49-52 (bunched/lapped-traffic laps, real duration 143-173s each)
   swung the drift to -249s by lap 52 — the Timing Tower was showing gaps
   from ~4 minutes later in the race than what the Circuit Map dots were
   depicting. Fixed by moving gap recomputation onto the position thread's
   own real-time clock (`lap_boundaries`, computed from the earliest
   starter's real `LapStartTime` per lap — the same convention a real
   Timing Tower's lap counter uses), with the old dispatch-loop trigger
   kept only as a fallback for sessions with no ingested position data.

Also added: `resolve_car_numbers()` shared helper (`_ingest_common.py`,
reused from `ingest_live_session.py`'s pattern), `--start-lap`/`--end-lap`
CLI flags, Redis pipelining for per-tick publishes (a sequential-round-trip
version measured ~30% slower than the intended 1.0s cadence).

### Checkpoint D — Strategy Wall / Undercut Panel sync
New `useCurrentLapHistoryEntry(sessionId, driverId)` hook
(`web/src/hooks/useStrategy.ts`) — returns the `StrategyPrediction` history
entry valid at a driver's current WS-reported lap, re-fetching whenever the
lap advances (queryKey includes the current lap number — caught and fixed
a staleness bug here during implementation, where the query would have
fetched once and frozen). `PitWindowCard` (compact/Strategy Wall variant)
and `UndercutThreatPanel` both render this lap-gated data when
replay/live is active (reduced detail — no exact projected-gap-seconds/
SHAP/window-range, since `StrategyPredictionHistoryEntry` doesn't carry
those fields), falling back to the existing live-recompute endpoints for
plain historical viewing. `usePitWindow`/`useUndercut` extended with an
`enabled` override so ~20 driver cards don't fire expensive live ML
recomputes while replay is driving the data instead.

Three manual-test findings investigated, all confirmed correct/expected
(not bugs):
- Near-zero pit probability across most drivers at British GP laps 43-45 —
  confirmed via direct DB query against real `StrategyPrediction` rows;
  plausible given each driver's real tyre-age context, and
  `safety_car_probability` has no way to foresee the real SC at lap 46-47.
- HUL stuck showing the old live-mode card format — confirmed HUL's last
  `lap_data` row is lap 37, entirely outside the 43-52 replay window, so no
  WS event ever arrives for them and the live/overview fallback is correct.
- Circuit map dots clustered — this one **was** a real bug, fixed in
  Checkpoint E (see below).

Found one unrelated pre-existing bug while investigating (not fixed, out
of scope, logged): `prediction_worker._run_inference` stores the tire_deg
model's raw `lap_time_delta` prediction into `StrategyPrediction
.tire_life_remaining` instead of the correctly-computed
`predicted_life_remaining` — confirmed via DB (values like `-1.749`).
Doesn't affect `pit_probability` or anything Day 43 exposes. See CLAUDE.md's
Deferred Wiring section.

### Checkpoint E — Circuit Map Panel
Original fix: new `GET /races/session/{session_id}` endpoint
(`race_service.get_race_by_session`, `useRaceBySession` hook) replacing
`useUpcomingRace()` as the outline/transform source — `useUpcomingRace()`
has nothing to do with whichever session is actually being viewed.

Then **four more rounds of real regressions**, all found via your testing:

1. **Position clustering + disappearing dots** — see Checkpoint C's fixes
   above (same root cause, same fix).
2. **Gaps/position desync** — see Checkpoint C's fixes above.
3. **`/race` (no session) vs. `/race/{session}` (explicit deep link)
   regression** — the `raceBySession`-always fix broke the fallback case
   (`/race` with nothing in the URL correctly falls back to the most
   recently completed race for the Timing Tower, but the Circuit Map
   should show the genuinely-upcoming race's countdown+circuit, not the
   fallback session's). Then fixing *that* broke the opposite case
   (`/race/{british-gp-id}`, an explicit link, showed Monza's outline under
   a countdown nobody asked for). Fixed with a new `isExplicitSession` prop
   (`RacePage` passes `Boolean(paramSessionId)`) and a new `"historical"`
   mode: outline follows `raceBySession` whenever `sessionId` means
   something specific (`"live"` or `"historical"`), `upcomingRace`
   otherwise (`"non-race"`/`"finished"`, the genuine idle-dashboard case).
4. **Dot glide smoothness** — see section 5 below; investigated thoroughly,
   not resolved, explicitly parked per your instruction.

Confirmed (not a bug): VER disappearing from the Timing Tower while their
dot kept moving — VER's `Position` is `NaN` starting exactly at lap 47
(their real retirement lap; `Time` is present, `Position` isn't) via direct
FastF1 data. The confusion was a side effect of the Checkpoint C fix #4
above: the console's `Lap N/Total` dispatch progress and the real-time
gaps/position clock are now deliberately on different clocks, so the
console log is no longer a reliable proxy for "what's currently on
screen."

### Checkpoint F — Final verification
Full suite run clean, twice (once mid-session, once as the final pass
after the last dot-glide attempt):
- `ruff check backend/` — all checks passed.
- `mypy backend/ --strict` — no issues, 130 source files.
- `make test-unit` (`pytest backend/tests/unit -m unit`) — 140/140 passing.
- `cd web && npx tsc -b` — clean.
- `cd web && npm run test` (vitest) — 17/17 passing (6 files).
- `cd web && npm run lint` (oxlint) — clean (2 pre-existing warnings in
  `ui/button.tsx`/`ui/form.tsx`, unrelated to this session).

**Manual verification confirmed by you**: Strategy Wall and Undercut Panel
switch correctly to lap-gated replay data; Circuit Map shows the correct
circuit in all four navigation scenarios (`/race` fallback, explicit deep
link, live, replay); gaps and position dots stay synchronized on real time
through a full replay including the bunched/lapped-traffic end-of-race
laps. Not confirmed as resolved: dot glide smoothness (see section 5).

## 2. What remains — original Day 43 spec Part 3.2, 4, 5, 6

None of this has been started. All of it depends on decisions/design
already made in the original spec (see conversation history for full
detail) — summarized here for a fast restart.

### Part 3.2 — CLI live-race safety guard
`replay_pipeline.py` must refuse to start via direct CLI invocation if a
live race is currently detected (check `f1:{season}:{round}:gaps` for a
live/fresh key, or the auto-detection dedup key — see section 4's Redis
key list). Non-negotiable per the original spec: running a replay
alongside real live ingestion would corrupt data and confuse users.

### Part 4 — Backend replay-control API + kill-switch
- **Shared live-race-detection utility** (used by both the guard endpoint
  below and the kill-switch) — check if `f1:{any_season}:{any_round}:gaps`
  exists with a fresh TTL, OR check auto-detection dedup key state.
- `GET /demo/replay/available` → `{"available": bool, "reason": str|null}`.
- `POST /demo/replay/start` — 409 if a live race is detected; 409 if
  another demo replay is already running (single global replay state,
  simplest sufficient design for a portfolio demo); accepts `session_id`
  validated against a hardcoded list of the 3 curated sessions (see
  section 3); launches `replay_pipeline.py` as a subprocess, tracks PID in
  Redis (`f1:demo:replay:pid`, `f1:demo:replay:session_id` — see section
  4); returns a `replay_id`.
- `POST /demo/replay/stop` — reads the PID from Redis, terminates the
  subprocess cleanly, clears the tracking keys.
- `GET /demo/sessions` — returns the 3 curated sessions with hardcoded
  metadata: `session_id, race_name, description, start_lap, end_lap,
  estimated_duration_minutes`.
- **Kill-switch**: if Celery Beat's `check_for_live_session`
  (`race_detection_worker.py`) detects a real race starting, it must check
  `f1:demo:replay:pid` first and force-stop any active demo replay before
  launching real live ingestion. Likely a small addition to
  `race_detection_worker.py` — propose the exact implementation when
  resuming, don't assume.

### Part 5 — Frontend replay selector UI
- On the Race page, when NOT in live mode: a "Watch a Replay" section with
  3 curated session cards (race name, description, fixed lap range shown
  as text, e.g. "Laps 15-25"), "Start Replay" button per card — no range
  slider, no custom selection.
- Call `GET /demo/replay/available` on page load — hide the whole section
  if `false`.
- While replay running: "Currently replaying: {race name}, Lap
  {current}/{end}" indicator; Timing Tower, Strategy Wall, Undercut Panel,
  Circuit Map, Lap Chart, Sector Heatmap should all already update in sync
  (that's what Checkpoints A-E built and verified) — Part 5 is UI/control
  only, not another sync fix; "Stop Replay" button.
- Desktop app: mirror the same feature (explicitly deferred from today's
  session by your own choice — not started, same as everything else here).

### Part 6 — Safety testing
1. `POST /demo/replay/start` returns 409 when a live race Redis key
   exists.
2. Starting a real live race (or simulating via the Redis key) while a
   demo replay is running correctly force-stops the replay with no
   orphaned subprocess.
3. `replay_pipeline.py` itself refuses to start via direct CLI invocation
   if a live race is detected.
4. All components stay in sync during a full curated-session replay —
   manual browser check (this part specifically is yours to do, per the
   original spec).

## 3. The 3 curated sessions

| Race | session_id | Circuit | Lap range | Why |
|---|---|---|---|---|
| British GP 2026 R9 | `7da820bf-5e8c-49bb-b19f-cdd88325af87` | Silverstone Circuit | 43-52 (race ends at 52) | Full Safety Car deployed ~lap 46-47, triggering a pit stampede (4→8→5 stops across laps 46-48), ALB running a 6-stop outlier strategy, field bunches behind leader LEC by laps 50-51 with lapped cars overtaking — visible through to the flag. |
| Belgian GP 2026 R10 | `da57b9fd-4976-4fce-91a1-c7d0aac9c619` | Circuit de Spa-Francorchamps | 14-23 | Two VSC periods land between laps 17-20, triggering a 7-stop cluster exactly at lap 20 — a clean undercut/overcut battle window. |
| Canadian GP 2026 R5 | `dd1a9280-1230-4f34-8b2d-f8b0256a3df4` | Circuit Gilles Villeneuve | 26-35 | VSC deployed between laps 29-31, triggering a 5-then-6-stop cluster — a second, differently-timed undercut fight at a different circuit. |

All 3 have `lap_data`, `tire_stints`, and `driver_positions` (1Hz, within
the lap ranges above) already ingested. Dutch GP R12 was deliberately
**not** chosen — it's the session with the known laps-1-8-never-ingested
gap bug.

## 4. How `replay_pipeline.py` works now

- **Two independent clocks, by design**: the main thread dispatches
  `process_lap`/`run_strategy_prediction` per driver at `--rate`-paced
  intervals (`fast`=5s/`normal`=30s/`slow`=90s/custom N, unchanged from
  before this session) — this is a display/testing pacing knob only. A
  **background thread** (`_run_position_timeline`, started via
  `threading.Thread`, `daemon=True`) streams both position updates AND gap
  recomputation on one shared **real-time** clock, reconstructed from
  FastF1's own `LapStartTime` data. These two clocks are deliberately
  decoupled — the console's `Lap N/Total` progress line reflects only the
  dispatch clock, not what's currently on screen (see Checkpoint E's VER
  finding above).
- **`--start-lap`/`--end-lap`** (new this session) restrict both the
  dispatch loop and the position/gaps timeline to an inclusive lap range —
  use these for the curated sessions' windows (table above), and for any
  bounded manual testing (a full 10-lap window takes 15-25 real minutes to
  play out; a 1-2 lap window is enough to verify wiring).
- **Startup sequence**: resolve season/round/session_type → load FastF1
  session (`laps=True`) → resolve+publish car numbers
  (`f1:{season}:{round}:driver:{id}:car_number`, 4h TTL) → fetch all
  `driver_positions` rows in range → build the absolute-time timeline
  (`_build_position_timeline`) → start the background thread → run the
  main dispatch loop.
- **Shutdown**: on normal completion, the main thread explicitly
  `position_thread.join()`s (waits for the full real-time window to
  finish) before exiting; on `KeyboardInterrupt`, a `threading.Event` signals
  the background thread to stop within ~1 tick, then a bounded `join(timeout=15)`
  in the `finally` block.
- **Redis keys written** (all TTL'd, see CLAUDE.md's Redis Cache Key
  Schema for the canonical list): `f1:{season}:{round}:gaps` (600s TTL —
  generous vs. live's 30s, since replay only refreshes it per real lap
  boundary, not continuously), `f1:{season}:{round}:car:{car_number}
  :position` (3s TTL, matching live), `f1:{season}:{round}:driver:{id}
  :car_number` (4h TTL).
- No CLI live-race guard exists yet (Part 3.2, see section 2) — running
  `replay_pipeline.py` today has no safety check against a real live race
  running concurrently.

## 5. Known issue — dot glide smoothness (parked, don't re-investigate blind)

Circuit Map dots still show brief pauses between movement, even after
three real attempts this session:
1. `--ease-in-out-strong` → `linear` CSS easing (fixed a decelerate-to-
   stop pulse, confirmed real via the curve's own math, but didn't fully
   solve it).
2. CSS transition duration retuned from under the poll interval to over
   it (900ms → 1100ms) — didn't help either, per your report.
3. Full rewrite to `requestAnimationFrame`-driven interpolation against
   `performance.now()` timestamps captured in the browser
   (`web/src/components/circuit/AnimatedDriverDots.tsx`, new file) —
   theoretically the most robust fix (no fixed duration to guess wrong),
   verified the underlying backend data is clean at every step (polled
   Redis directly at 100ms granularity, confirmed smoothly-varying data
   every ~1.0s) — still didn't fully resolve it per your report.

Your own read: likely network/render-timing variance in your environment,
not a data or logic bug — the backend data pipeline was directly verified
correct at every single attempt. **Don't re-open this without a specific
new lead** (e.g. if it's still visible next session AND you have a new
theory, or if it's gone and this note is now moot). If it's worth another
look, the next real lever (not yet tried) would be increasing position
data density above 1Hz — see `ingest_position_data.py`'s downsampling —
but that's a real ingestion-time change with its own row-volume tradeoffs,
not a quick tweak.

## 6. Files changed this session

**New files:**
- `backend/migrations/versions/20260827_add_driver_positions_table.py`
- `backend/scripts/ingest_position_data.py`
- `web/src/hooks/useRaceBySession.ts`
- `web/src/components/circuit/AnimatedDriverDots.tsx`
- `docs/day43-handoff.md` (this file)

**Modified files:**
- `backend/models/telemetry.py` — `DriverPosition` model
- `backend/models/race.py` — `driver_positions` relationship
- `backend/models/driver.py` — `driver_positions` relationship
- `backend/models/__init__.py` — `DriverPosition` export
- `backend/scripts/ingest_historical.py` — `load_session()` gained an
  optional `telemetry` param (default `False`, existing callers unaffected)
- `backend/scripts/_ingest_common.py` — `resolve_car_numbers()` helper
- `backend/scripts/replay_pipeline.py` — car-number resolution, gap
  computation, position timeline, threading, `--start-lap`/`--end-lap`
  (extensive — see section 4)
- `backend/services/race_service.py` — `get_race_by_session()`
- `backend/apis/v1/races.py` — `GET /races/session/{session_id}`
- `CLAUDE.md` — Deferred Wiring entry (`tire_life_remaining` bug), Redis
  Cache Key Schema entry, API Versioning list entries
- `web/src/types/strategy.ts` — `StrategyPredictionHistoryEntry`/
  `StrategyPredictionHistoryResponse`
- `web/src/api/strategy.ts` — `getStrategyHistory()`
- `web/src/hooks/useStrategy.ts` — `useCurrentLapHistoryEntry()`,
  `usePitWindow`/`useUndercut` gained an `enabled` override
- `web/src/components/strategy/PitWindowCard.tsx` — replay-aware compact
  rendering
- `web/src/components/strategy/UndercutThreatPanel.tsx` — replay-aware
  rendering, new `ReplayThreatRow`
- `web/src/__tests__/PitWindowCard.test.tsx` — mock updated for the new hook
- `web/src/api/race.ts` — `getRaceBySession()`
- `web/src/components/circuit/CircuitMapPanel.tsx` — session-scoped
  outline resolution, `isExplicitSession`/`"historical"` mode,
  `AnimatedDriverDots` integration (extensive — see Checkpoint E)
- `web/src/pages/RacePage.tsx` — passes `isExplicitSession` prop
- `web/src/hooks/useDriverPositions.ts` — poll interval 2000ms → 1000ms
- `web/src/index.css` — motion tokens (`--duration-dot-glide` added then
  removed once superseded by JS interpolation)
- `web/DESIGN.md` — Motion section rewritten (multiple rounds, tracks the
  glide-smoothness investigation)

No git commands have been run this session (status, log, diff, etc.
included) per explicit instruction throughout. Everything above is
working-tree-only. Staging and committing remain your step.

## 7. Resume prompt

When resuming, paste this to Claude Code:

> Read docs/day43-handoff.md first. We are resuming Day 43 exactly where
> we left off. Checkpoints A-F are complete and verified — do not
> re-implement or re-verify anything in section 1. We're picking up at the
> original spec's Part 3.2 (CLI live-race safety guard), Part 4 (backend
> replay-control API + kill-switch), Part 5 (frontend replay selector UI),
> and Part 6 (safety testing) — none of which has been started. Propose a
> checkpoint plan for these before writing any code, same as Day 43's
> original planning approach.

Claude Code should read section 2 in full before proposing a plan, and
should not re-open section 5 (dot glide smoothness) without a specific new
lead.

---

## 8. Part 2 — COMPLETE (Checkpoints 1-5, 2026-08-28)

Spec Part 3.2 / 4 / 5 / 6 delivered across 5 gated checkpoints. Full
verification green throughout: `ruff check backend/`, `mypy backend/
--strict` (138 files), `pytest backend/tests/unit -m unit` (166 passed),
`pytest backend/tests/integration/test_demo_endpoints.py` (8 passed),
`cd web && npx tsc -b` (clean), `npm run test` (22 passed, 7 files),
`npm run lint` (only the 2 pre-existing `ui/button.tsx` / `ui/form.tsx`
warnings).

### Checkpoint 1 — shared live-race detection + CLI guard (Part 3.2)
- **New `backend/services/live_race_detection.py`** — `detect_live_race`
  (async) / `detect_live_race_sync` (sync), returning
  `LiveRaceStatus(is_live, reason)`. Two signals, either one positive =
  live: (a) any `f1:*:*:gaps` key whose remaining TTL ≤ 90s (a live
  ingestor actively refreshing its 30s-TTL key — a replay's own key has a
  600s TTL refreshed only per lap boundary, so it never trips this; the
  trailing-anchored pattern also excludes the `:gaps:final` / `:last_good`
  siblings); (b) any `f1:*:*:R:auto_ingestion_triggered` dedup key.
- **`replay_pipeline.py`** — `_guard_against_live_race()` runs at the top
  of `main()`, before `replay()`: on a positive result it logs the reason
  and `sys.exit(1)`. No bypass flag, by design. Also backstops the API
  path (the `/demo/replay/start` subprocess re-hits `main()`).
- Tests: `test_live_race_detection.py` (8), `test_replay_pipeline_guard.py`
  (3).

### Checkpoint 2 — backend replay-control API (Part 4)
- **New** `backend/schemas/demo_schema.py`, `backend/services/demo_service.py`,
  `backend/apis/v1/demo.py` (registered in `apis/v1/__init__.py`).
- `demo_service.CURATED_SESSIONS` — the 3 sessions from section 3,
  hardcoded (session_id, race, circuit, description, lap window, est.
  duration).
- Endpoints (all rate-limited): `GET /demo/sessions`,
  `GET /demo/replay/available` (**live-race gate only** — does not consider
  whether a replay is already running), `GET /demo/replay/status`,
  `POST /demo/replay/start` (202, `Depends(get_current_user)`),
  `POST /demo/replay/stop` (`Depends(get_current_user)`).
- **Single global state** in one JSON key `f1:demo:replay:state` (see
  CLAUDE.md Redis Cache Key Schema): `start_replay` does an atomic
  `SET … NX` slot claim → 409 if already held, 409 on a live race, 422 if
  the session isn't curated; then launches `replay_pipeline.py` detached
  (`--start-lap/--end-lap` from curated metadata, `--rate fast`,
  `--no-alert-worker`) and writes the full payload (incl. PID). Claim is
  deleted if the launch raises `OSError`. `stop_replay` reads the PID,
  `os.kill(pid, SIGTERM)` (tolerates `ProcessLookupError`/
  `PermissionError`), clears the key; 404 if nothing running.
- **`replay_pipeline.py`** — `_reraise_sigterm_as_interrupt` handler
  installed in `replay()`, so SIGTERM (from the stop endpoint or the
  kill-switch) routes into the existing graceful `KeyboardInterrupt`
  shutdown instead of a hard kill.
- CLAUDE.md updated: Redis Cache Key Schema (`f1:demo:replay:state`), API
  Versioning list (5 endpoints).
- Tests: `test_demo_service.py` (12 unit), `test_demo_endpoints.py` (8
  integration — unknown-session 422, live-race 409, already-running 409,
  claim rollback on launch failure, auth on POST, full start→status→stop
  roundtrip).

### Checkpoint 3 — Celery Beat kill-switch (Part 4)
- **`race_detection_worker.check_for_live_session`** — after the dedup
  claim succeeds (a real race is launching) and before
  `_launch_ingestion_subprocess`, calls `_force_stop_demo_replay(client)`:
  reads `f1:demo:replay:state`, `os.kill(pid, SIGTERM)`s the replay
  subprocess, deletes the key. Tolerates a dead pid or a bare NX-claim
  sentinel.
- `demo_service._STATE_KEY` promoted to the public
  `DEMO_REPLAY_STATE_KEY` so the worker and service share one definition.
- CLAUDE.md: kill-switch paragraph added to the Auto Race Detection
  section.
- Tests: 3 added to `test_race_detection_worker.py` (force-stops an active
  replay; tolerates a dead pid; no signal when no replay running).

### Checkpoint 4 — frontend replay selector UI (Part 5, web only)
- **New** `web/src/types/demo.ts` (+ `types/index.ts` re-export),
  `web/src/api/demo.ts`, `web/src/hooks/useDemoReplay.ts`
  (`useCuratedSessions`, `useReplayAvailable`, `useReplayStatus` — polls 5s
  running / 20s idle, `useStartReplay` / `useStopReplay`),
  `web/src/components/demo/ReplaySelectorPanel.tsx`.
- Panel mounted in `RacePage.tsx` `<main>` above `<CircuitMapPanel>`.
  Renders nothing when `isLive` or `available.available !== true`. Idle: a
  "Watch a Replay" card with the 3 curated session cards (race name,
  `Laps X–Y · ~N min`, description, "Start Replay"). Running: "Currently
  replaying: {race} — Lap {current}/{end}" + "Stop Replay".
- **Current lap is derived frontend-side** from `useSessionGaps` (max
  `lap_number` across the gaps the replay itself publishes) — no backend
  progress field, per the plan.
- **On successful start: `toast` + `navigate(/race/{session_id})`** so the
  session-scoped panels (timing tower, circuit map, strategy) actually
  render the replayed session — without this the user starts a replay and
  sees nothing change on their current view.
- Tests: `ReplaySelectorPanel.test.tsx` (5 — hidden when live; hidden when
  unavailable; 3 cards with lap ranges; start fires the mutation with the
  right session_id; running indicator shows `Lap 47/52` and stop fires the
  mutation).

### Checkpoint 5 — safety testing + verification (Part 6)
- **Part 6 item 1** (start → 409 on a live-race key): covered by
  `test_demo_endpoints.py::test_start_conflicts_with_live_race` +
  `test_demo_service.py::test_start_rejects_when_live_race`.
- **Part 6 item 2** (a launching real race force-stops the replay, no
  orphan): covered by `test_race_detection_worker.py`'s 3 kill-switch
  tests. No orphaned `alert_worker` child is possible — the API launches
  the replay with `--no-alert-worker`.
- **Part 6 item 3** (CLI refuses to start during a live race): covered by
  `test_replay_pipeline_guard.py` (guard exits 1 when live; passes when
  not; `main()` aborts before `replay()`).
- **Part 6 item 4** (manual browser sync check across all panels during a
  full curated replay): **the user's to do** — not an automated check.

### Post-CP5 fix — live-race guard false positive (2026-08-28)

Found during manual verification: `POST /demo/replay/start` returned 409
`"live timing feed active for 2026 round 10"` with no live race running.
Root cause was in CP1's detector. `replay_pipeline.py` wrote
`f1:{s}:{r}:gaps` with a 600s TTL and never deleted it on shutdown, so
after a replay ended the key lingered; for the last ~90s before it
expired, its decaying TTL (<=90s) was indistinguishable from a live
ingestor's freshly-refreshed 30s-TTL key. A Belgian GP (round 10) replay
run earlier that evening left exactly this.

Fixed (5 items; `ruff`, `mypy --strict` 138 files, `pytest -m unit` 170
passed, `test_demo_endpoints.py` 8 passed):
1. `replay_pipeline._compute_lap_gaps` adds `"source": "replay"` to the
   gaps payload — the live `_publish_live_gaps` payload has no such key;
   ignored by `SessionGapsResponse` (extra fields), no consumer changes.
2. `live_race_detection.detect_live_race` / `_sync` GET + JSON-parse each
   `f1:*:*:gaps` key and skip any with `source == "replay"`, regardless
   of TTL. The `0 < TTL <= 90` check still applies to non-replay keys.
3. `replay_pipeline.replay()`'s `finally` deletes its own
   `f1:{s}:{r}:gaps` key on exit (normal completion or SIGTERM); only a
   SIGKILL skips it, and item 2's marker covers that.
4. `demo_service.get_replay_status` self-heals a stale
   `f1:demo:replay:state` (a replay that finished on its own never clears
   it — only `/stop` and the kill-switch do): if the tracked PID is dead
   (`_process_is_alive` via `os.kill(pid, 0)`, POSIX only — skipped on
   Windows where `os.kill` would terminate the target), the key is
   deleted and status reports not-running.
5. Regression tests: `source:replay` gaps key at TTL 30 is NOT live
   (async + sync); `get_replay_status` self-heal + still-alive paths.

### Post-CP5 fix round 2 — replay still not starting + Stop button gone (2026-08-28)

Manual re-verification: replay started (state key set) but nothing
updated, and the panel/Stop button vanished. Investigation:
- The replay subprocess (launched by `POST /demo/replay/start` inside
  **`docker-backend-1`**, child of uvicorn) exited immediately as a
  **zombie** (`/proc/<pid>/status` → `State: Z`). Its own
  `_guard_against_live_race()` was `sys.exit(1)`-ing.
- Round-1's `source:replay` marker was not enough. A **third** writer of
  `f1:{s}:{r}:gaps` exists: `telemetry_service.get_session_gaps`'s
  `@cacheable(ttl=8)` cache-aside, fired every ~8s by the frontend's
  `useSessionGaps` poll for **any** session on screen. That payload has
  **no** `source` and a ≤8s TTL — indistinguishable from a live key under
  the TTL heuristic. Sampling `f1:2026:10:gaps` showed it blinking in/out
  at TTL 6→2 with a source-less body while the Belgian GP page was open.
- `GET /demo/replay/available` uses the same detector → returned
  `{available:false}` intermittently → `ReplaySelectorPanel` (line 46)
  rendered `null`, taking the Stop button with it. `useReplayAvailable`
  had no `refetchInterval`, so a single bad reading stuck for the session.
- `_process_is_alive` used `os.kill(pid, 0)`, which **succeeds for a
  zombie**, so `get_replay_status` never self-healed the stuck state.

Fixed (A-E; `ruff`, `mypy --strict` 138 files, `pytest -m unit` 176
passed, `web tsc -b` + `npm run test` 22 passed):
- **A** — `ingest_live_session._publish_live_gaps` stamps `"source":
  "live"`. `detect_live_race` / `_sync` now treat a gaps key as a live
  race **only** when its payload has `source == "live"` — the TTL
  heuristic is removed entirely. Replay (`source:replay`) and the
  `@cacheable` write (no source) are both ignored. Auto-detection dedup
  key check unchanged.
- **B** — `_process_is_alive` is now zombie-aware: non-blocking
  `os.waitpid(pid, WNOHANG)` reaps an exited child (→ not alive), then
  `os.kill(pid, 0)`, then a `/proc/<pid>/stat` state check (`Z`/`X` →
  not alive). Windows still short-circuits to `True`. New `_proc_is_zombie`
  helper; `_WNOHANG = getattr(os, "WNOHANG", 1)` for Windows import safety.
- **C** — `useReplayAvailable` gets a 30s `refetchInterval` so a transient
  bad reading self-corrects instead of hiding the panel.
- **D** — running-indicator row is `flex flex-wrap` + `shrink-0` on the
  button, so the Stop button wraps below the text instead of being pushed
  out of an `overflow-x-auto` ancestor.
- **E** — `test_live_race_detection.py` reworked around `source:live` vs
  `source:replay` vs source-less (`@cacheable`) payloads incl. the
  regression case; 5 new `_process_is_alive` / `_proc_is_zombie` tests
  (windows / reaped-child / gone / zombie / running); `test_demo_service.py`
  + `test_demo_endpoints.py` live-race fixtures now write a `source:live`
  payload.

### Post-CP5 fix round 3 — replay subprocess crashes on FastF1 cache (2026-08-28)

Manual re-verification: replay "started" but nothing progressed; page kept
showing full (unscoped) session data; UI reverted after ~2 min. Running
the exact command in `docker-backend-1`:
```
File ".../replay_pipeline.py", line 283, in _load_fastf1_session
    fastf1.Cache.enable_cache(settings.fastf1_cache_dir)
NotADirectoryError: Cache directory does not exist! ... create it first.
```
`replay_pipeline._load_fastf1_session` called `fastf1.Cache.enable_cache()`
**without first creating the dir** — its two siblings both `os.makedirs(...,
exist_ok=True)` on the line above (`ingest_historical.py:88`,
`ingest_live_session.py:695`). `/tmp/fastf1_cache` doesn't exist in the
backend container, so the subprocess died ~1s after launch, before
publishing anything → frontend fell back to unfiltered historical data
(Day 42 "no WS data" fallback). The "~2 min" was just zombie-detection
self-heal latency. Never hit before because Days 43 A-F ran the script
from the host venv (warm cache). **This same crash would hit
`ingest_live_session.py` on a real auto-detected live race** launched from
the worker container.

Fixed (both parts; `ruff`, `mypy --strict` 138 files, `pytest -m unit` 177
passed):
- **Part 1 (code)** — `replay_pipeline` gains `import os` and
  `os.makedirs(settings.fastf1_cache_dir, exist_ok=True)` before
  `enable_cache`, plus a `logger.info` before `.load()` making the one-time
  ~30-60s first-run download visible rather than looking hung. New test
  `test_load_fastf1_session_creates_missing_cache_dir`.
- **Part 2 (infra)** — `infra/docker/docker-compose.yml`: new named volume
  `fastf1_cache` mounted at `/tmp/fastf1_cache` on `backend`, `worker`, and
  `beat`, so the per-session FastF1 download happens once and survives
  container recreates. `docker compose down && up -d` run to apply;
  verified the volume is attached (`/dev/sdd on /tmp/fastf1_cache ext4`),
  `_load_fastf1_session` creates the dir in a fresh container, and
  `GET /api/v1/demo/replay/available` returns `{"available":true}`.
- Pre-warming was declined: the first "Start Replay" per curated session
  still has a silent ~30-60s FastF1 download (log line explains it), then
  it's cached permanently.

### Post-CP5 fix round 4 — env regression + root-owned FastF1 volume (2026-08-28)

Manual re-verification of round 3: toast "Something went wrong" on replay
start; Strategy Wall showed "No live race session active" instead of replay
progression. Two causes, one mine-in-testing and one a real bug in round 3:

1. **`docker compose up -d` was run without `--env-file .env`.** `make dev`
   always passes it (the Makefile comment says why: Compose looks for `.env`
   next to the compose file, `infra/docker/`, not repo root). Without it,
   every `${AWS_*:-}` / `${SENTRY_DSN:-}` etc. resolved to **empty** in
   `backend`/`worker`/`beat`. `GET /strategy/{session}/overview` (Strategy
   Wall) does a synchronous S3 model download →
   `botocore.exceptions.ParamValidationError: Invalid bucket name ""` → HTTP
   500. `StrategyOverviewGrid` renders its `drivers.length === 0` empty
   state ("No live race session active") — it is NOT a live-only gate, it
   just can't render its (replay-aware) `<PitWindowCard compact>` children
   without the driver list from `/overview`. `main.tsx`'s global
   `QueryCache.onError` toasts `getApiErrorMessage(error)` with no fallback
   → its bare default "Something went wrong".
2. **Round 3's `fastf1_cache` named volume is root-owned; containers run as
   `f1` (uid 999).** FastF1's `enable_cache` opens a `requests_cache`
   SQLite DB inside the dir → `sqlite3.OperationalError: unable to open
   database file` → the replay subprocess crashes on startup, publishes
   nothing. (Round 3's "verification" only mocked `enable_cache`, so it
   missed this.)

Fixed:
- **`infra/docker/Dockerfile.backend` + `Dockerfile.worker`** — added
  `RUN mkdir -p /tmp/fastf1_cache && chown f1:f1 /tmp/fastf1_cache` before
  `USER f1`. Docker copies an image dir's ownership into an empty named
  volume on first mount, so the volume becomes `f1`-writable.
- Recreated: `down` → `docker volume rm docker_fastf1_cache` (targeted —
  NOT `down -v`, which would wipe `postgres_data`) →
  `docker compose -f infra/docker/docker-compose.yml --env-file .env up --build -d`.

**Always recreate the local stack with:**
```
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d
```
(or `make dev`). Omitting `--env-file .env` silently resolves AWS creds,
`SENTRY_DSN`, and the Slack/Grafana vars to empty — the app boots but S3
model loading (and anything else needing a real secret) fails at runtime.

### Not done / still open
- **Desktop app replay selector** — Part 5 explicitly scoped web-only;
  the desktop mirror was deferred and is not started.
- **CLAUDE.md "Current Project Phase" block** — left for the user to
  update per the usual workflow (the `update: phase tracker after Day X`
  commit).
- Everything in section 2 that isn't Part 3.2/4/5/6 (Day 40 Fly.io, etc.)
  is untouched.

### Files — Part 2
**New:** `backend/services/live_race_detection.py`,
`backend/schemas/demo_schema.py`, `backend/services/demo_service.py`,
`backend/apis/v1/demo.py`, `backend/tests/unit/test_live_race_detection.py`,
`backend/tests/unit/test_replay_pipeline_guard.py`,
`backend/tests/unit/test_demo_service.py`,
`backend/tests/integration/test_demo_endpoints.py`,
`web/src/types/demo.ts`, `web/src/api/demo.ts`,
`web/src/hooks/useDemoReplay.ts`,
`web/src/components/demo/ReplaySelectorPanel.tsx`,
`web/src/__tests__/ReplaySelectorPanel.test.tsx`.

**Modified:** `backend/scripts/replay_pipeline.py`,
`backend/workers/race_detection_worker.py`, `backend/apis/v1/__init__.py`,
`backend/tests/unit/test_race_detection_worker.py`, `CLAUDE.md` (Redis
Cache Key Schema + API Versioning list + Auto Race Detection kill-switch
note — NOT the Current Project Phase block), `web/src/types/index.ts`,
`web/src/pages/RacePage.tsx`.
