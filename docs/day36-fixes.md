# Day 36 — Dutch GP Live Race Dry Run: Bugs Found & Fixed

**Date:** August 23, 2026
**Context:** First live end-to-end test of `ingest_live_session.py` against a real
race (2026 Dutch Grand Prix, round 12, Zandvoort). Race ran 13:00–~15:22 UTC;
this session's fixes were applied and verified live, both during and after the race.

---

## Bug 1: signalrcore `access_token_factory: None` crash

**File(s):** `backend/scripts/ingest_live_session.py` (`_build_connection`)

**Root cause:** In `no_auth=True` mode (the default — no F1TV subscription
configured), the code set `"access_token_factory": None` in the SignalR
connection options dict. `signalrcore`'s `HubConnectionBuilder.with_url`
validation checks whether the key is *present* in the options dict, not
whether its value is truthy — a present-but-`None` value still triggers
`TypeError: access_token_factory is not function`. Reproduced directly
against the installed library with the exact options dict used.

**Fix:** Only set the `access_token_factory` key at all when `no_auth` is
`False`; omit it entirely in no-auth mode instead of setting it to `None`.

**Impact:** Unblocked the live connection entirely — this crashed on every
connection attempt before any data could flow, in both the manual test run
and the auto-launched production subprocess (which had been crash-looping
since 13:04 UTC, silently dropping the first ~20 minutes of the race).

**Verified:** Reproduced the crash directly against `signalrcore`'s installed
source with the pre-fix options dict; confirmed the fix by building the same
dict in isolation and observing no exception.

---

## Bug 2: FastF1 REST `SessionNotAvailableError` for the 2026 session

**File(s):** N/A (diagnosis only — no code changed for this specific finding)

**Root cause:** `fastf1.get_session(...).load()`'s `session_info`/
`driver_info` REST calls returned `SessionNotAvailableError` even ~20+
minutes into the live race, and the `livetiming-mirror.fastf1.dev` fallback
has no 2026 data (already documented in CLAUDE.md's deferred items). This
meant `fastf1_session.drivers` was always `[]`, so any code relying on it
(the original car-number→driver mapping approach) never resolved.

**Fix:** No fix to FastF1 itself — this became the reason Bug 4 (DriverList
snapshot) needed to exist as an independent, live-feed-native resolution
path rather than depending on FastF1's REST calls at all.

**Impact:** Established that FastF1's historical REST API is unusable as a
live-session data source (mid-race), which shaped the design of Bugs 4 and
8's fixes. Notably, this same API **did** work correctly for building the
final-standings recovery in Bug 11, once the race had actually concluded —
the failure is time-bound to "still live," not permanent for the session.

**Verified:** Reproduced directly (`fastf1_session.drivers == []` confirmed
live, ~20 minutes and again ~24 minutes post-race-start); confirmed the same
call succeeded after the race ended (Bug 11).

---

## Bug 3: F1 feed boolean sentinels crashing the reconnect loop

**File(s):** `backend/scripts/ingest_live_session.py`
(`_handle_driver_list`, `_handle_timing_data`, `_as_dict` helper)

**Root cause:** F1's live timing feed mixes non-driver sentinel keys (e.g. a
bare `true` for a "keyframe"/unchanged-field marker, referred to throughout
this session as the `"_kf"` quirk) into the same dicts as real per-car
entries — in both `DriverList` and `TimingData` payloads. Unfiltered
iteration (`payload.items()`, `entry.get("Sectors")` etc.) hit a bare `bool`
where a `dict` was expected and crashed with `AttributeError: 'bool' object
has no attribute 'get'`. Critically, `x or {}` (used to default a possibly-
missing sub-field) is *also* broken for this case: `True or {}` evaluates to
`True`, not `{}`, so that existing defensive pattern didn't actually defend
against it. The crash happened inside signalrcore's own websocket dispatch
thread (uncaught), which surfaced as a full connection teardown and
immediate reconnect — confirmed live as a reconnect loop firing every ~2.3
seconds, with the duplicate-log-line count growing on each cycle.

**Fix:** Added `isinstance(entry, dict)` guards before iterating suspect
payloads, and a shared `_as_dict(value)` helper (`value if isinstance(value,
dict) else {}`) used everywhere a `x or {}` pattern previously existed.

**Impact:** Stopped an active reconnect-storm against F1's live timing
servers (rapid, repeated full handshake+negotiate cycles), which is both a
reliability risk (each fresh connection has to re-resolve driver mapping
before any data can flow again) and a potential rate-limiting risk on F1's
side from hammering their edge with reconnects.

**Verified:** Reproduced the exact crash via a standalone unit-style script
feeding a synthetic `{"1": {...}, "_kf": true}` payload through
`_handle_driver_list`/`_handle_timing_data` before the fix (crashed) and
after (processed cleanly, driver entries resolved, sentinel skipped). Live:
connection stayed open 90+ seconds with zero reconnects post-fix, versus
the prior every-~2.3s loop.

---

## Bug 4: DriverList snapshot discarded (missing `on_invocation`)

**File(s):** `backend/scripts/ingest_live_session.py`
(`_on_subscribe_result`, `start`)

**Root cause:** F1's live timing hub returns each subscribed topic's full
initial snapshot as the **return value of the `Subscribe` RPC invocation
itself**, not as a subsequent `"feed"` push. The original code called
`self._connection.send("Subscribe", [_TOPICS])` with no `on_invocation`
callback, so that snapshot — including `DriverList`, the only source of the
car-number→driver_id mapping once FastF1's REST calls were unusable (Bug
2) — was silently discarded. `DriverList` rarely changes mid-session, so no
delta ever arrived on `"feed"` either, meaning `car_number_to_driver_id`
stayed permanently empty for the whole session: every lap was silently
dropped (logged as "Skipping lap for unmapped car number").

**Fix:** Pass `on_invocation=self._on_subscribe_result` to the `Subscribe`
call; added `_on_subscribe_result` to read `message.result`'s per-topic
snapshot dict and route `DriverList` (and later, `TimingAppData` and
`TimingData`'s `Position` field — Bugs 5 and 8) through the same handlers
used for live "feed" deltas.

**Impact:** This was the core unblocking fix for the entire live dry run —
without it, zero laps could ever be attributed to a driver, regardless of
every other fix in this document.

**Verified:** Live: all 22 drivers' car numbers resolved correctly from the
Subscribe snapshot immediately on connect (logged one "Resolved car number
N -> driver ... (CODE)" line per driver); laps began accumulating in
`lap_data` within seconds of connecting.

---

## Bug 5: Tyre compound hardcoded to `"UNKNOWN"`

**File(s):** `backend/scripts/ingest_live_session.py`
(`_handle_timing_app_data`, `_latest_stint`), `backend/schemas/telemetry_schema.py`
(`TireStintCreate`), `backend/workers/telemetry_worker.py` (`record_tire_stint`)

**Root cause:** `_handle_timing_data`'s `raw_lap` dict had `"compound":
"UNKNOWN"` hardcoded literally. F1's live feed carries real compound data on
a completely different topic (`TimingAppData`'s `Lines.{car}.Stints[].
Compound`), which was never subscribed to at all — `_TOPICS` didn't include
it.

**Fix:** Subscribed to `TimingAppData`; added `_handle_timing_app_data` to
track each car's current compound (handling both the list-shaped initial
snapshot and the index-keyed-dict shape later diffs use) and feed it into
`raw_lap.compound`. Added a new `record_tire_stint` Celery task + `TireStintCreate`
schema to also persist new stints to the `tire_stints` table.

**Impact:** Tyre compound badges in the timing tower and sector heatmap now
show real data (`HARD`/`MEDIUM`/`SOFT`) instead of a placeholder for every
lap ingested after this fix landed. `tire_stints` (previously always empty
for live sessions) now gets real rows.

**Verified:** `SELECT DISTINCT compound FROM lap_data WHERE ... AND compound
!= 'UNKNOWN'` → `HARD`/`MEDIUM`/`SOFT` confirmed live; `tire_stints` query
showed real rows with correct `compound`/`stint_number`.

**Known limitation:** `ON CONFLICT DO NOTHING` on `lap_data` means laps
already recorded before this fix landed keep their `"UNKNOWN"` compound
permanently — accepted, not backfilled.

---

## Bug 6: Sector times S1/S2 lost (single-message extraction)

**File(s):** `backend/scripts/ingest_live_session.py` (`_handle_timing_data`,
`_sector_accumulator`)

**Root cause:** F1's `TimingData` is a diff stream — a single message
carries whichever sector was *just* crossed, not all three at once. The
original code read `entry.get("Sectors")` fresh from only the one message
where `NumberOfLaps` incremented (the lap-completion trigger) — which
happens to share a message with sector 3 (the finish line), but sectors 1
and 2 had already arrived and been discarded in earlier, separate messages.
Confirmed live: every recorded lap had `sector3_seconds` populated but
`sector1_seconds`/`sector2_seconds` always `NULL`.

**Fix:** Added a per-car `_sector_accumulator` dict that collects each
sector value as its individual delta arrives (every `TimingData` message,
not gated behind lap completion), and reads the accumulated set — not the
single triggering message — when a lap completes.

**Impact:** Sector heatmap and lap-times chart now show all three sectors
for laps ingested after this fix, not just S3.

**Verified:** Direct simulation (sector 1 in one synthetic message, sector 2
in a second, lap-completion + sector 3 in a third) produced a `raw_lap` with
all three sectors populated. Live: `SELECT lap_number, sector1_seconds,
sector2_seconds, sector3_seconds FROM lap_data ...` showed all three
populated for new laps.

**Known limitation:** Same `ON CONFLICT DO NOTHING` caveat as Bug 5 — laps
recorded before this fix keep `NULL` sectors 1/2 permanently.

---

## Bug 7: Gap calculation wrong (naive cumulative-sum subtraction)

**File(s):** `backend/services/telemetry_service.py` (`_compute_session_gaps`),
`backend/schemas/telemetry_schema.py` (`DriverGap`),
`web/src/components/telemetry/LiveTimingTower.tsx` (`computeGapLabels`),
`web/src/types/telemetry.ts` (`DriverGap`)

**Root cause:** `_compute_session_gaps`'s DB-reconstruction path computed
`gap_to_ahead_seconds`/`gap_to_behind_seconds` as a raw subtraction of
`SUM(lap_time_seconds)` between adjacent sorted drivers, with no check for
whether they were on the *same lap*. When one driver had completed fewer
laps (a lap down), the subtraction compared incompatible amounts of race
distance, producing nonsensical — sometimes negative — values (confirmed
live: `-54.17s`). This also corrupted the frontend's running "gap to
leader" cumulative sum for every position behind the first such boundary,
so the visible ordering/spacing looked wrong even though raw position
numbers (1, 2, 3…) were technically sequential.

**Fix:** When adjacent sorted rows have different `lap_number`s, set
`gap_to_ahead_seconds`/`gap_to_behind_seconds` to `None` and report a new
`laps_behind` field (the lap deficit to the car immediately ahead) instead.
Frontend `computeGapLabels` switches to a "lapped mode" the first time it
sees `laps_behind > 0`, displays `"+N LAP(S)"` using an accumulated lap
count, and never reverts to a time-based gap for the rest of the field
(lap deficits only grow going down the order).

**Impact:** No more negative/nonsensical gap values in the timing tower;
lapped drivers correctly show `"+1 LAP"` etc. instead of a garbage number.

**Verified:** Live `/gaps` response scanned programmatically for negative
values (found zero, post-fix, vs. a confirmed `-54.17s` pre-fix); unit-level
simulation reproducing the exact adjacent-pair scenario; `LiveTimingTower.
test.tsx` (3 tests) updated and passing.

**Superseded by Bug 9's finding:** this fix corrected the *lap-boundary*
case but not a deeper flaw in the SUM-based approach itself — see Bug 9.

---

## Bug 8: Position only in Subscribe snapshot, not on `TimingData` diffs

**File(s):** `backend/scripts/ingest_live_session.py`
(`_update_gap_state`, `_on_subscribe_result`, `_publish_live_gaps`)

**Root cause:** While building the live gap-publishing path (`GapToLeader`/
`IntervalToPositionAhead` parsed directly from `TimingData`, replacing
DB reconstruction), the `Position` field was found — by directly inspecting
raw feed messages — to be sent by F1 **only** in the one-time Subscribe
snapshot, never on incremental diffs (unlike `GapToLeader`/
`IntervalToPositionAhead`, which do stream on every diff). Since only
`_handle_timing_data` (the "feed" delta handler) parsed `Position`, and the
Subscribe snapshot's `TimingData` was deliberately *not* replayed through it
(to avoid mis-processing already-completed laps — see Bug 4's note), no car
ever got a `position` value, and `_publish_live_gaps` silently never wrote
anything (empty position list, early return).

**Fix:** Extract `Position` from `_on_subscribe_result`'s one-time
`TimingData` snapshot too, via a shared `_update_gap_state` helper (safe —
unlike full lap-completion processing, this has no lap-completion side
effects). Later `Position` changes (rare — mostly overtakes) arrive
correctly on subsequent diffs once the initial value is seeded.

**Impact:** Unblocked the entire live-gaps publishing feature (Bug 9) — no
positions meant no rows ever appeared in `f1:{season}:{round}:gaps`.

**Verified:** Live: `f1:2026:12:gaps` populated with all 22 drivers'
positions within seconds of connecting, immediately after this fix; `curl
.../gaps` returned correctly ordered, non-null-position results end to end.

---

## Bug 9: Cache poisoning — 24h TTL cached empty live-session data

**File(s):** `backend/services/driver_service.py` (`get_driver_laps`,
`_is_session_live`, `_resolve_season_round`)

**Root cause:** `get_driver_laps` (backs the lap-times chart and sector
heatmap, via `@cacheable(ttl=86400, ...)`) was first called for this session
before any live laps existed yet, caching `{"items": [], "total": 0}` for a
full 24 hours — the TTL's underlying assumption ("historical lap data is
immutable once ingested") is correct for a completed session but wrong for
one still being live-ingested. Confirmed: every one of ~22 drivers' cache
entries was stuck at `total: 0` despite the DB having 50+ real rows per
driver by the time this was found.

**Fix:** Replaced the static-TTL `@cacheable` decorator with a hand-rolled
cache-aside (mirroring the existing `race_service.get_current_race` pattern
for exactly this "TTL depends on a runtime condition" need): checks
`f1:{season}:{round}:gaps` (written by live ingestion) to decide between a
30-second TTL (live session, changes every lap) and the original 86400s
(historical/completed session).

**Impact:** Lap-times chart and sector heatmap, empty for every driver for
the rest of the live session before this fix, now show real, current data —
and won't repeat this failure mode on a future live session.

**Verified:** Deleted the 50 poisoned cache keys, confirmed `total: 61` (was
`0`) for NOR immediately after; confirmed TTL is `30`s while
`f1:2026:12:gaps` exists and reverts to `86400`s once it doesn't (tested
both directions live).

**Known regression introduced by this fix, found in this audit:**
`backend/tests/unit/test_driver_service.py::test_get_driver_laps_paginates_correctly`
now fails — its mock only stubs 2 `db.execute` calls, but the new code path
makes a 3rd (`_resolve_season_round`, inside `_is_session_live`). Not yet
fixed as of this audit — see the accompanying test-suite report.

---

## Bug 10: Gap publishing only on change (reliability)

**File(s):** `backend/scripts/ingest_live_session.py` (`_handle_timing_data`,
`_publish_live_gaps`)

**Root cause:** After Bug 8's fix, `_publish_live_gaps` was still only
called when `_update_gap_state` reported an actual value change for at
least one car in that message. F1 can resend an *identical* gap string for
a stretch (two cars holding a stable gap to three decimal places), which is
common enough in practice that the live gaps key's 30-second TTL
intermittently lapsed even during a genuinely live, ongoing race — observed
directly: `f1:2026:12:gaps` sat at `TTL -2` (expired) for 20+ seconds at a
stretch before self-recovering.

**Fix:** Call `_publish_live_gaps()` unconditionally at the end of every
`_handle_timing_data` invocation, not gated on whether anything changed.
`TimingData` messages themselves arrive frequently regardless of any single
field's value, so this keeps the TTL reliably warm.

**Impact:** `driver_service._is_session_live` (Bug 9) and `telemetry_
service._compute_session_gaps`'s live-vs-final-vs-DB tiering (Bug 11) both
depend on this key's presence being a reliable "is this session live right
now" signal — an intermittently-lapsing key would have made both flap
between correct and incorrect behavior unpredictably.

**Verified:** Polled `TTL f1:2026:12:gaps` every few seconds across a 90+
second window post-fix — stayed continuously warm (7 → 30 → …), no gaps,
versus the pre-fix pattern of repeated expiry.

---

## Bug 11: Final standings lost after session end (DB reconstruction unreliable)

**File(s):** `backend/services/telemetry_service.py` (`get_session_gaps`,
`_compute_session_gaps`) — plus a one-off manual data-recovery step (not a
code change) to snapshot today's actual final standings.

**Root cause:** Once the race ended, `ingest_live_session.py` stopped
refreshing `f1:2026:12:gaps` (nothing left to publish), and
`_compute_session_gaps`'s DB-reconstruction fallback took over — exposing a
deeper flaw than Bug 7's lap-boundary case: **every driver's recorded lap
history started at lap 9**, not lap 1 (live ingestion wasn't fully working
until partway through the race — see Bugs 1–4). `SUM(lap_time_seconds)`
from lap 9 onward silently measures "change in gap since lap 9," not the
true absolute race gap, and different drivers' unrecorded laps 1–8 differ
enough to invert real finishing order. Confirmed directly: the SUM-ascending
order (NOR, HAM, LEC, RUS, ANT, PIA) exactly matched the tower's wrong
podium, while the real result (confirmed via FastF1's now-available
post-race historical data) was NOR, ANT, RUS, HAM, LEC, PIA — RUS and ANT
inverted with HAM/LEC, corrupting the actual podium classification, not
just gap magnitudes.

**Fix:**
1. One-off recovery: FastF1's REST results API (which had failed with
   `SessionNotAvailableError` throughout the live race — Bug 2) succeeded
   once queried post-race; used its authoritative per-lap `Position`/`Time`
   data to build a correct final-standings snapshot (positions 1–15;
   positions 16+ excluded — FastF1 itself flagged a data-quality gap there,
   "missing information about deleted laps," pending official Ergast
   results) and wrote it to a new key, `f1:{season}:{round}:gaps:final`
   (30-day TTL).
2. Durable fix: `get_session_gaps` now checks `gaps:final` (after the
   existing `@cacheable` check of the live `gaps` key, before falling
   through to DB reconstruction) — a 3-tier order: live → final → DB
   fallback (DB reconstruction is now reachable only for a session that was
   genuinely never live-ingested).

**Impact:** Timing tower now shows the correct final classification (NOR,
ANT, RUS, HAM, LEC, PIA, LAW…) instead of a DB-reconstruction artifact that
inverted the actual P2/P3 result.

**Verified:** `curl .../gaps` confirmed exact match to the real result
across all 7 top positions with correct gap-to-leader values; `gaps:final`
round-tripped through `SessionGapsResponse.model_validate` cleanly;
`gaps:last_good` (previously poisoned with the same wrong order) self-
corrected via `@cacheable`'s normal write-through once `gaps:final` started
being read.

---

## Bug 12: Upcoming-race panel showing the just-finished Dutch GP indefinitely

**File(s):** `backend/services/race_service.py`
(`_fetch_upcoming_race_from_db`, `_fetch_upcoming_race_from_fastf1`,
`get_or_create_race` now reused from `_ingest_common.py`)

**Root cause:** `/races/upcoming`'s DB query used `race_date >= today`
(date-only, no time-of-day) — true all day regardless of whether the race
already ran. `CircuitMapPanel.tsx`'s `mode === "finished"` branch renders
`"Next: " + upcomingRace.race_name`, so it kept showing "Next: Dutch Grand
Prix" for the rest of the day after the race concluded. A naive fix
(`race_date > today`) would have broken the *pre-race* countdown, which
depends on the same `>=` query finding today's race before it starts — that
non-regression constraint was explicit and had to be preserved.

A second bug was found live while testing the fix: the FastF1-schedule
fallback path (`_fetch_upcoming_race_from_fastf1`, reached once the DB path
correctly started skipping the concluded race) had the *identical* `>=`
flaw, and — because it built a new `Race(...)` row unconditionally instead
of checking for an existing one — it **created a duplicate DB row** for
round 12 when it re-resolved Dutch GP a second time. Caught and cleaned up
before it caused any downstream confusion (verified nothing referenced the
duplicate before deleting it).

**Fix:** Both DB and FastF1-fallback candidate lookups now check whether a
same-day race has concluded — via `f1:{season}:{round}:gaps:final`
(authoritative, from Bug 11) first, falling back to a 3-hour buffer since
the R session's scheduled start (F1's own regulatory race-duration cap) for
a race never live-ingested. A race still in the future, or today but not
yet started, is returned immediately without needing either check — so the
pre-race countdown path is untouched. The FastF1 fallback now uses
`get_or_create_race` (idempotent by season+round_number, already used
elsewhere in the ingest scripts) instead of a raw insert, closing the
duplicate-row risk generally, not just for this one incident.

**Impact:** Circuit map panel now correctly shows the real next race
(Italian GP, round 13, 2026-09-06) once Dutch GP concludes, instead of
looping "Next: Dutch Grand Prix" for the rest of the day. Pre-race countdown
behavior (shown before a race starts) is unaffected — confirmed directly.
Live-race mode (moving driver dots) is unaffected — it's driven by a
separate signal (`useDriverPositions`/`isLive`), not by this endpoint at
all.

**Verified:** `curl /races/upcoming` → round 13 Italian GP, correct future
`scheduled_start`; confirmed exactly one round-12 and one round-13 row in
the DB (duplicate cleaned up); directly tested the pre-race case (`now` = 1
hour before Dutch GP's own start) → still correctly returns Dutch GP,
unchanged; new unit test
`test_fetch_upcoming_race_from_db_skips_concluded_same_day_race` added,
reproducing this exact scenario; full `test_race_service.py` suite (25
tests) passing.

---

## Cross-cutting notes

- **Desktop/mobile parity gap (not fixed this session):** `desktop/src/
  components/telemetry/LiveTimingTower.tsx` and `desktop/src/components/
  overlay/RaceOverlay.tsx` have their own independent copies of `DriverGap`
  and gap-label logic (per the existing Desktop Sync Protocol — these are
  hand-copied, not shared). Neither was updated with Bug 7's `laps_behind`
  handling. Desktop won't show the *wrong* negative-gap numbers (the
  backend no longer sends them to anyone), but will show `"—"` for every
  driver behind a lapped car instead of `"+1 LAP"` — see the accompanying
  audit report for full detail and risk assessment. `mobile/` has the stale
  type only, no runtime consumer, zero functional risk.
- Every fix that touched `ingest_live_session.py` required killing and
  relaunching the live ingestor process inside the worker container to take
  effect (bind-mounted source, but a long-running Python process doesn't
  hot-reload); two of those relaunches also required a full `docker compose
  restart worker` specifically because new Celery tasks (`record_tire_
  stint`) needed re-registration.
