# Day 43 Handoff — Demo Replay (Checkpoints A-F complete, Parts 3.2/4/5/6 not started)

Written at the end of the Day 43 session. Read this in full before resuming
— it's the single source of truth for exactly where things stand.
Checkpoints A-F (this session's actual scope) are done and verified. The
original Day 43 spec's Part 3.2/4/5/6 (replay control API, kill-switch,
frontend selector UI, safety testing) were deliberately deferred to a
follow-up session partway through planning and have **not been started at
all** — don't confuse "Checkpoint F done" with "the whole Day 43 spec done."

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
