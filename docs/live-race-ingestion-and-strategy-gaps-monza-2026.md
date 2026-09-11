# Live Race Ingestion & Strategy-Feature Gaps — Italian GP 2026 (Monza)

> **Status: investigation only, NOT fixed.** Discovered 2026-09-11 reviewing
> the local Docker DB after a real live-ingested race — Italian GP 2026,
> Round 13, Monza, 53 laps — that the stack ingested end-to-end while the
> user watched the UI during the actual race. This is the first real-world,
> full-race live-ingestion run this project's UI has been directly observed
> against (as opposed to a replayed/synthetic session or a post-hoc
> historical ingest), and it surfaced five distinct, real problems the user
> noticed live, all independently confirmed here against the real DB and the
> real source code — not assumed from reading code alone. (Issues D and E
> started life as one conflated issue; a user follow-up after the first
> draft surfaced a fact that split it into two genuinely separate bugs —
> see Issue D's own correction note.)
>
> Every fact in this document was captured via a live query against the
> local Postgres/TimescaleDB stack on 2026-09-11, or a direct read of the
> current source files named. No fixes have been attempted — this is a
> discovery/root-cause document only, for a future session to plan and
> implement against. Read the Anchor Prompt at the end before starting that
> work.

---

## 0. Session & Data Reference

Real identifiers, so any future session can reproduce every query in this
document exactly:

| Field | Value |
|---|---|
| Race | Italian GP 2026, Round 13 |
| Circuit | Autodromo Nazionale Monza |
| Race.id | `22b873c3-3b85-4395-83bb-7a299f1f45ce` |
| Race.status | `scheduled` (never flipped to `completed` — expected for a live-ingested race that was never re-processed by `ingest_historical.py`; same pattern as the already-documented Zandvoort R12 case in CLAUDE.md) |
| race_date | 2026-09-06 |
| R session.id | `3ddc84bd-f10e-4870-9e98-631d79695beb` |
| Real total race distance | **53 laps**, confirmed via `MAX(LapData.lap_number)` across the whole field post-race |

The stack was up and live-ingesting for the entire real race. All findings
below come from querying `lap_data`/`tire_stints` directly against this
session, plus reading the exact ingestion/service/frontend code paths that
produced what's in the DB and what the UI rendered from it.

---

## 1. Issue A — Pit-window recommendation exceeded the real race length (predicted lap 78 on a 53-lap race)

### What the feature is supposed to do

`PitWindowCard` ("Recommended: Lap N") should show a pit lap that is, at
worst, the last lap of the race — recommending a stop that can't physically
happen (25 laps past the chequered flag) is never correct, live or
historical.

### What it currently does

At some point around/after lap 38 (based on the exact arithmetic below),
the UI showed **"Recommended: Lap 78"** for a 53-lap race.

### Root cause — two independent layers, both real

**Layer 1: two different "pit lap" fields exist, and the live UI path
silently prefers the unbounded one.**

- `recommended_pit_lap` — from `strategy_service.compute_pit_recommendation`
  (`backend/services/strategy_service.py`), which **is correctly capped**:
  `max_pit_lap = min(state["lap_number"] + PIT_WINDOW_LOOKAHEAD_LAPS, state["total_laps"])`
  (`PIT_WINDOW_LOOKAHEAD_LAPS = 15`).
- `predicted_pit_lap` — the API-renamed form of `StrategyPrediction.
  optimal_pit_lap`, computed in `prediction_worker._run_inference`
  (`backend/workers/prediction_worker.py:731`):
  ```python
  "optimal_pit_lap": lap_number + max(int(predicted_life_remaining), 1),
  ```
  **This has no bound against race length anywhere.**
  `predicted_life_remaining` caps at `tire_deg_model.MAX_LOOKAHEAD_LAPS = 40`.
  At `lap_number=38`, a pegged `predicted_life_remaining=40` gives exactly
  `38 + 40 = 78` — matching the observed value exactly. (Whether the driver
  in question was actually at lap 38 specifically wasn't independently
  re-confirmed against the exact UI screenshot/timestamp — the arithmetic
  match is strong circumstantial evidence, not a re-run of the literal
  original request, since the live moment can't be replayed after the fact.)

  Predicted-life-remaining pegging at the 40-lap cap is itself a
  well-documented, pre-existing model-quality characteristic — see
  `docs/tire-deg-model-quality-and-rival-pit-behavior.md` (the CP1-CP5
  investigation earlier this same day) for the full history; this document
  does not re-litigate that model-quality question, only how its output
  reaches the UI completely unbounded here.

  The frontend (`web/src/hooks/useStrategy.ts`, `viewFromHistoryEntry`)
  picks between the two: `pitLap: entry.recommended_pit_lap ??
  entry.predicted_pit_lap` — it only falls back to the unbounded field when
  the capped one is `null`.

**Layer 2 (the more important one): `recommended_pit_lap` is null far more
often during a LIVE race than its own docstring implies, making the
"rare" fallback actually common.**

`_compute_recommendation_fields`'s own docstring says `recommended_pit_lap`
is `None` when "`current_lap >= total_laps`, race essentially over" — framed
as an end-of-race edge case. But `total_laps` itself, throughout the entire
live per-lap pipeline (`_current_state` in `strategy_service.py`,
`_resolve_inference_context` in `prediction_worker.py`), is computed as:

```python
total_laps_query = select(func.max(LapData.lap_number)).where(LapData.session_id == session_id)
total_laps = (await db.execute(total_laps_query)).scalar_one() or lap.lap_number
```

i.e. **"the highest lap number recorded for this session so far."** During a
race that is still in progress, this is not an estimate of the real race
distance — it's essentially a restatement of "how far has the race gotten,"
which tracks within a lap or two of `current_lap` for every driver, all the
time. That means `max_pit_lap = min(current_lap + 15, total_laps)` collapses
to **≈ `current_lap`** for almost the entire live race, not just near the
end — so `pit_laps = np.arange(current_lap + 1, max_pit_lap + 1)` is empty
(`n_candidates == 0`) far more often than the "race essentially over"
framing suggests. This was reasoned from the code, not independently
measured against this exact race's Redis/DB timeline — see below.

**Also relevant, discovered along the way, not yet connected with certainty:**
`useCurrentLapHistoryEntry`'s `isReplayActive` flag (which gates whether
`PitWindowCard` reads the persisted-history path at all) is driven by
`useLiveTelemetry`'s WebSocket feed (`liveEvent !== undefined`) — it is
`true` for a genuinely live race exactly the same way it's `true` during
Demo Replay, despite the name suggesting it means Demo Replay specifically.
This isn't itself a bug, but it means every "isReplayActive"-gated code path
in the frontend needs to be read as "replay OR live," not "replay only,"
when reasoning about live-race behavior — worth keeping in mind for the rest
of this document too.

### Files involved

- `backend/workers/prediction_worker.py` — `_run_inference` (the unbounded
  `optimal_pit_lap`), `_compute_recommendation_fields` (where
  `recommended_pit_lap` gets set to `None`)
- `backend/services/strategy_service.py` — `_current_state` (the `total_laps`
  proxy), `compute_pit_recommendation` (the correctly-capped search that
  still depends on that proxy being right)
- `web/src/hooks/useStrategy.ts` — `usePitRecommendation`,
  `useCurrentLapHistoryEntry`, `viewFromHistoryEntry`'s fallback
- `web/src/components/strategy/PitWindowCard.tsx` — renders `view.pitLap`

### What needs investigating / researching before fixing

1. **Independently confirm Layer 2's frequency claim with real data**, not
   just code-reasoning — instrument or query how often `recommended_pit_lap`
   was actually `None` across this real race's persisted `StrategyPrediction`
   rows (the table still has this race's rows; a direct query can settle
   this precisely) before assuming how bad/common the fallback really was.
2. **Design a real `total_laps` source.** CLAUDE.md already documents that
   no table stores race distance anywhere (`Race`/`Circuit`/`Session` all
   lack a `total_laps` column) — this has been worked around before by using
   `MAX(lap_number)` as a proxy for a *completed* session, where it's
   trivially correct. For a *live* session it fundamentally isn't. Real
   options, not pre-decided: (a) add a real `total_laps` column, populated
   from Ergast/FastF1's own scheduled-laps data at race-weekend setup time
   (most correct, but needs a data source audit — does Ergast reliably carry
   this pre-race?); (b) a live-only fallback heuristic (e.g. a per-circuit
   typical lap count table); (c) something else. This decision affects more
   than just the pit-window feature — anything computing "laps remaining"
   live inherits the same proxy problem (worth auditing for other call
   sites of this exact query pattern while in there).
3. **Decide whether `optimal_pit_lap`/`predicted_pit_lap` should be clamped
   independently**, as a defensive backstop, regardless of the `total_laps`
   fix above — even a correct `total_laps` elsewhere doesn't help if this
   specific field is never bounded by anything.
4. **Re-check `isReplayActive`'s naming/semantics** while in this code —
   whether it should be renamed for clarity, or whether live and replay
   genuinely should keep sharing this exact code path (they may legitimately
   want to diverge once `total_laps` is fixed, since a live session and a
   replay of a *completed* session have different total-laps reliability).

---

## 2. Issue B — Undercut threat alert ignored overall race state

### What the feature is supposed to do

An undercut-threat alert should represent a *realistic* strategic threat —
not just "if both cars hypothetically pit right now, who wins the next few
laps on tyre freshness," independent of whether that hypothetical pit stop
is something either car would actually make at that point in the race.

### What it currently does

At lap 38 of 53 (15 laps remaining), an alert fired for ANT showing **100%**
undercut-threat probability from VER (the car behind) — despite there being
little realistic likelihood of another stop being made by either car that
late, per the user's own read of the race situation.

### Root cause

`_undercut_overcut_probability` (`backend/services/strategy_service.py`,
~line 1635) computes a fixed, race-state-blind comparison every time it's
called:

- `pitting_now_driver_id` pits **this lap**, runs `UNDERCUT_PROJECTION_LAPS
  = 5` laps on a fresh tyre.
- `pitting_next_lap_driver_id` stays out **one more lap**, then pits and
  runs the remaining 4 laps on a fresh tyre.
- `UNDERCUT_MONTE_CARLO_SIMS = 200` Gaussian-noise draws turn the
  deterministic tyre-delta projection into a probability.

This is a pure, symmetric tyre-physics question — "does 5 laps of fresher
rubber beat 5 laps of older rubber" — computed **identically regardless of
what lap of the race it is**. Nothing here asks:

- whether there's enough remaining race distance for a pit-and-recover
  strategy to make sense at all (a fresh-tyre time advantage over a 5-lap
  window is real regardless of how many laps are left, but *whether a team
  would actually pit for it* depends heavily on how many laps remain to
  cash in that advantage — the model has no notion of this trade-off);
- whether the "threatening" driver's own `pit_probability` (already computed
  elsewhere, by `pit_predictor`) suggests they're likely to pit again at
  all;
- `Circuit`/session-level total-laps context is simply never passed into
  this calculation's *strategic* reasoning, only into the tyre-delta
  projection's own lookahead math (via `_project_stint_delta`'s
  `now_state["total_laps"]`/`next_state["total_laps"]` arguments, which
  only prevent projecting past the field-max-so-far, not "is this
  realistic").

`evaluate_threats` (`backend/services/alert_service.py`) then just checks
`undercut_score > UNDERCUT_ALERT_THRESHOLD` (`0.5`) with no additional
race-state gate before dispatching — so a "100%, but physically
meaningless at this point in the race" score reaches the user exactly as
confidently as a genuinely actionable one earlier in the race would.

### Files involved

- `backend/services/strategy_service.py` — `_undercut_overcut_probability`,
  `UNDERCUT_PROJECTION_LAPS`/`UNDERCUT_MONTE_CARLO_SIMS` constants
- `backend/services/alert_service.py` — `evaluate_threats`,
  `UNDERCUT_ALERT_THRESHOLD`

### What needs investigating / researching before fixing

1. **Define what "race state" should mean here, concretely** — this is a
   real design decision, not pre-made: a hard remaining-laps cutoff below
   which undercut alerts simply don't fire? A confidence discount that
   scales down as remaining laps shrink? Cross-referencing the threatening
   driver's own `pit_probability` (if it's low, an undercut "threat" from
   them is not credible regardless of the tyre-physics number)? Some
   combination?
2. **Check whether this is a `race_simulator`-adjacent problem too.**
   `docs/tire-deg-model-quality-and-rival-pit-behavior.md`'s CP6 (not yet
   built) already considers a "resilience layer independent of
   pit_predictor's own calibration" for the Monte Carlo simulator — this
   undercut-alert gap may be a related, possibly-shared piece of the same
   underlying "the system has no sense of overall race-strategy plausibility"
   limitation. Worth reading that document before scoping a fix here, to
   avoid solving overlapping problems twice with two different mechanisms.
3. **Real-world validation data needed.** Before/after this fix, the same
   kind of real-stint measurement used throughout the tire-deg-model
   investigation (real historical races, real undercut attempts, did they
   actually happen and succeed) would be the right acceptance test — not
   currently built for this specific question.
4. Confirm whether `alert_worker.py`'s separate FCM-push threshold path
   (`_PIT_PROBABILITY_ALERT_THRESHOLD = 0.5`, a different mechanism
   entirely per CLAUDE.md) has any related gap — not checked in this
   session, flagged only as a "look here too" note.

---

## 3. Issue C — Retired driver (LEC) never left the timing tower

### What the feature is supposed to do

Once a driver retires, they should stop appearing as an active competitor
in the live timing tower (and anywhere else driven by the same live
position/gap state) for the remainder of the race — not continue to occupy
a position/gap slot based on stale pre-retirement data.

### What it currently does, confirmed against the real DB

LEC's entire footprint in `lap_data` for this session is **one row**:

```
lap_number=1, lap_time_seconds=NULL, position=3, track_status=NULL,
compound=SOFT, is_valid=True, created_at=2026-09-06 13:05:04 UTC
```

`tire_stints` shows one stint, never closed out: `(stint_number=1,
compound=SOFT, start_lap=1, end_lap=NULL)`.

This confirms LEC's real retirement happened very early (consistent with
"retired at lap 4" — the DB simply has no further completed-lap events to
record after that, which is expected/correct: you cannot record "lap 2, 3,
4" for a car that never crosses those finish lines). What's *not* expected
is that the user says LEC kept appearing in the timing tower for the rest
of the 53-lap race.

### Root cause

`ingest_live_session.py`'s `_update_gap_state` evicts a car from the live
gap-tracking state (`_car_live_gap_state`) **only** when F1's feed sends the
exact string `"RETIRED"` in that car's `GapToLeader` field:

```python
_RETIRED_MARKER = "RETIRED"
...
gap_to_leader_raw = _extract_string_field(entry.get("GapToLeader"))
if gap_to_leader_raw is not None and gap_to_leader_raw.strip().upper() == _RETIRED_MARKER:
    was_present = self._car_live_gap_state.pop(car_number, None) is not None
    return was_present
```

If that exact marker never arrives for a given retirement — the car's feed
entry simply stops updating some other way, or F1 signals retirement
through a different field this ingestor doesn't check — this eviction never
fires, and the car's last real state (LEC's `position=3` snapshot) stays in
`_car_live_gap_state` indefinitely. `_publish_live_gaps()` then republishes
that frozen entry to Redis on **every single TimingData message for the
rest of the race**, unconditionally (this "publish every message regardless
of whether anything changed" behavior is itself a deliberate, documented
fix for a *different* bug — a Redis-TTL staleness issue — so it isn't
itself wrong, it's just what makes a frozen retiree's entry visible
continuously rather than only briefly).

This exact eviction mechanism is documented as having been validated
before, against a real session with 3 retirements
(`verify_live_feed_parity.py`, referenced in this function's own
docstring) — so this isn't a "the mechanism has never worked" situation,
more likely "F1's real feed doesn't always signal a retirement via this one
specific string," which the existing validation didn't happen to exercise.

### Files involved

- `backend/scripts/ingest_live_session.py` — `_update_gap_state`,
  `_publish_live_gaps`, `_RETIRED_MARKER`

### What needs investigating / researching before fixing

1. **Get a real recorded feed sample of LEC's actual retirement** from this
   race (if the raw feed was logged/cached anywhere — check
   `FASTF1_CACHE_DIR` or equivalent) to see exactly what F1 *did* send for
   LEC around lap 1-4, instead of guessing. This is the single most
   valuable next step — it would either confirm "a different retirement
   signal exists and needs handling" or reveal something else entirely
   (e.g. a genuinely missing/dropped message).
2. **Consider a robustness backstop independent of any specific marker
   string** — e.g. evicting a car whose `GapToLeader`/position hasn't
   updated in N real seconds/messages while every other car keeps
   advancing, as a fallback for whatever signal(s) F1's feed uses for
   retirement that aren't the literal `"RETIRED"` string. Needs research
   into what signals real F1 retirements can look like (there may be
   several: `"RETIRED"`, a `Status`/`KnockedOut`-type field elsewhere in
   the payload, `Position.z`'s own `Status` field noted-but-unused in
   `_handle_position_data`'s docstring, or simply silence).
3. **Check whether STR and ALO — who also stopped short in this same race**
   (STR: 26 laps, ALO: 23 laps, both real DNFs per the lap coverage table
   below) **showed the same frozen-timing-tower symptom**, or whether only
   LEC did — if only LEC, that's a strong hint the feed genuinely behaved
   differently for LEC's specific retirement (e.g. a mechanical failure vs.
   a collision vs. a driver-retired-car-on-track scenario might send
   different signals), which would focus the investigation considerably.
4. Confirm whether this same gap affects the **sector times chart** the
   user also mentioned for LEC — that chart reads real `lap_data` rows
   (confirmed in Issue D below), so LEC's actual plotted data should be
   just the one lap-1 point, not a continuously-updating stream; if the
   user saw LEC's sector chart look "live" throughout the race, that may
   point to a *different*, frontend-side stale-selection bug rather than
   this backend eviction gap — worth clarifying/re-observing next race
   before assuming this is the same root cause for both UI surfaces.

---

## 4. Issue D — Field-wide bogus lap-4 time (real, but NOT why VER's charts were empty — see Issue E)

> **Correction:** this section originally conflated two separate things.
> The user clarified afterward that during the race, **every other
> driver's** sector heatmap and lap-times chart updated correctly, lap by
> lap — only VER's showed no data, all race. That rules out this section's
> original theory (a shared, field-wide outlier visually flattening every
> chart) as the explanation for the reported symptom, since it would have
> broken every driver's chart equally, not just one. The bogus lap-4 value
> below is still real and still worth fixing, but it is a **separate,
> lower-severity issue** from what the user actually observed — see Issue E
> for the finding that actually explains the reported symptom.

### What the feature is supposed to do

A driver's lap-times and sector-times charts should plot their real,
physically-plausible lap-by-lap data — an implausible value (if one is ever
ingested) shouldn't be trusted/plotted raw alongside real ones.

### What it currently does, confirmed against the real DB

VER's data is actually complete: 53 rows, 52 real (non-null) lap times —
same as everyone else. **Every single driver in the field** shows an
obviously-bogus lap time on lap 4, all clustered in the same narrow range:

```
RUS 1958.319   COL 1957.224   VER 1956.913   HAM 1956.22   HUL 1956.009
ANT 1955.709   GAS 1955.174   BOR 1954.83    TSU 1954.517  PIA 1954.257
OCO 1954.058   SAI 1953.793   LIN 1953.462   BEA 1953.349  NOR 1953.175
LAW 1952.849   PER 1952.583   ALB 1952.319   STR 1952.267  BOT 1952.109
ALO 1948.683
```

~1955 seconds ≈ 32.6 minutes — for every driver, on the same lap, within a
~10-second spread of each other. That pattern (whole-field, same lap,
tightly clustered) is the signature of a real session stoppage (most likely
a red flag) rather than a per-driver data error. `track_status` is `NULL`
for all 1052 `lap_data` rows in this session, so it can't be used to
independently confirm a red flag was flagged — that's itself a related gap,
noted below.

**Confirmed by direct user observation, correcting this section's own
earlier draft(s):** during the race, every driver's lap-times chart *other
than VER's* rendered correctly — including the lap-4 anomaly itself, which
showed as a real, visible spike to ~30 minutes on the chart, before the
chart correctly resumed showing normal lap times for the rest of the race.
So the chart handled this outlier exactly as it should given the (bad) data
it was fed — a visible but non-breaking spike, not a squashed/unreadable
chart. (An earlier draft of this section guessed it would cause "a visual
glitch" without specifying what that would look like; this is the actual,
now-confirmed, correct behavior of the chart given bad input — the chart
itself is not at fault here.) This remains a real, worth-fixing data-quality
issue on its own terms (an implausible value silently marked
`is_valid=True` is a genuine data-integrity problem — see the `is_valid`
concern in "What needs investigating" below, which has value independent of
any UI symptom), but it should not be framed as a charting bug — it isn't
one, as far as this race's real behavior shows.

### Root cause

`ingest_live_session.py`'s `_handle_timing_data` builds `lap_time_seconds`
directly from F1's own `LastLapTime.Value` field, via `_parse_lap_time`,
with **no plausibility/sanity check of any kind**:

```python
"lap_time_seconds": _parse_lap_time(last_lap.get("Value")),
...
"is_valid": True,
```

`is_valid` is **hardcoded to `True` for every live-ingested lap**,
regardless of the value — there is no equivalent here of
`ingest_historical.py`'s reliance on FastF1's own `IsAccurate` flag. F1's
feed most likely genuinely reported this value (a red-flag-inflated
session-clock delta, not a parsing artifact of this codebase) — the parser
itself is working correctly; the gap is that nothing downstream ever
questions an implausible result before storing and serving it.

**Separately, minor, not the cause of either chart symptom:** the
standalone `SectorTime` table (`backend/models/telemetry.py`) has **zero
rows for this session, and zero rows in the entire database, for any
session, ever**. Confirmed this is genuinely dead/unused, not merely sparse
— `driver_service.py`'s laps endpoint (the one the sector chart actually
reads) queries `lap_data.sector1_seconds`/`sector2_seconds`/
`sector3_seconds` directly, never `SectorTime`. Worth flagging as a
separate, real observation (either finish wiring it or remove it), but not
part of either chart incident's root cause.

### Files involved

- `backend/scripts/ingest_live_session.py` — `_handle_timing_data`,
  `_parse_lap_time`
- `backend/models/telemetry.py` — `SectorTime` (the unused table, tangential)
- `backend/services/driver_service.py` — confirmed as the real data source
  for the laps/sector endpoint (reads `lap_data`'s inline columns)

### What needs investigating / researching before fixing

1. **Confirm the red-flag hypothesis directly**, don't assume it — check
   whether Monza 2026 R actually had a red flag (real-world race result/
   news, or F1's `RaceControlMessages` topic if this ingestor ever
   subscribes to it — currently it does not, per the topic list in
   `_on_feed`) around lap 4. If confirmed, this is expected/correct
   *session* behavior that the *ingestion* needs to handle gracefully, not
   a data-source error to chase further.
2. **Design the actual fix, once the cause is confirmed** — options, not
   pre-decided: (a) a plausibility cap on `lap_time_seconds` at ingestion
   time (e.g. anything over some threshold gets marked `is_valid=False`
   rather than silently trusted); (b) subscribing to `RaceControlMessages`
   to detect red-flag periods explicitly and handle affected laps
   specially (more correct, more work); (c) computing `is_valid` from some
   real signal instead of the current hardcoded `True`, more generally —
   this last one has broader value beyond just this incident, since
   `is_valid` being meaningless for every live-ingested lap likely affects
   more than charts (e.g. any downstream training-data export that trusts
   `is_valid` as a real filter would silently include every live-ingested
   session's anomalies too).
3. **Check whether the frontend charts have their own defensive outlier
   handling at all** (a reasonable second layer of defense regardless of
   the ingestion-side fix) — not reviewed in this session; only the backend
   data path was traced.
4. **`track_status` being NULL for the whole live session** is worth its
   own small investigation — does `ingest_live_session.py` simply never
   populate it (a live-ingestion-wide gap, separate from this specific
   incident), and if so, does anything downstream depend on it being real
   for a live session (e.g. `safety_car_model`'s live inference path, or
   the wet-tyre `wet_track` flag)?

---

## 5. Issue E — VER's lap/sector charts showed no data all race, while every other driver's updated correctly

### What the feature is supposed to do

Every driver's lap-times chart and sector heatmap should update every lap
during a live race, identically in mechanism regardless of which driver is
selected.

### What it currently does

Per the user's direct observation: VER's sector heatmap and lap-times chart
showed **nothing at all** for the entire race — not even the lap-4 spike
every other driver's chart correctly showed (see Issue D). Every other
driver's chart updated correctly, lap by lap, including plotting that same
anomalous value as a real spike before resuming normal data. VER's
underlying DB data is confirmed complete and correct (Issue D above) — this
is a **display/caching** problem, not a data problem, and it is specific to
VER (or, more precisely, to whichever driver's cache-population request
happened to land at the wrong moment — see below).

"Nothing at all" (not a partial/degraded chart, not even the one point
every other driver showed) is itself a meaningful clue: it's consistent
with the frontend receiving a genuinely **empty** dataset for VER, not a
malformed or partially-missing one — which lines up exactly with the known
failure mode described below (a cached `{"items": [], "total": 0}`
response), rather than, say, a rendering bug that would more plausibly
produce a partial or garbled chart instead of a total blank.

### Root cause — a known bug class, already documented once, with a real gap still open in it

`driver_service.get_driver_laps` (the function backing the lap-times/sector
data the charts render) already has a hand-rolled fix for exactly this
shape of bug, and its own docstring documents having hit it before:

```python
DRIVER_LAPS_TTL_SECONDS = 86400
# Live session laps change every ~1-2 minutes (one new lap per driver per
# lap). Confirmed live (2026 Dutch GP dry run): the 86400s TTL above cached
# an EMPTY result (queried before any laps existed yet) and then served that
# stale {"items": [], "total": 0} for the rest of the race, silently masking
# every real lap ingested afterward — 24h is only safe for a session that
# will never change again.
DRIVER_LAPS_LIVE_TTL_SECONDS = 30
```

```python
ttl = (
    DRIVER_LAPS_LIVE_TTL_SECONDS
    if await _is_session_live(client, db, session_id)
    else DRIVER_LAPS_TTL_SECONDS
)
```

So a genuinely live session is *supposed* to always get the short 30s TTL,
self-correcting within half a minute even if the first-ever request for a
given driver/page caught an early/incomplete snapshot. The gap: **this
entirely depends on `_is_session_live()` answering correctly at the exact
moment each cache entry is populated.**

```python
async def _is_session_live(client, db, session_id) -> bool:
    """Whether ingest_live_session.py is actively publishing gaps for this session.
    f1:{season}:{round}:gaps is written by the live ingestor's
    _publish_live_gaps (30s TTL, refreshed on every relevant TimingData
    update — see ingest_live_session.py) — its mere presence is a reliable
    live-vs-historical signal, cheaper than checking session status.
    """
    ...
    return await cache_get(client, f"f1:{season}:{round_number}:gaps") is not None
```

`f1:{season}:{round}:gaps` carries only a 30-second TTL, refreshed by
`_publish_live_gaps()` on every TimingData message the ingestor processes
— which normally keeps it warm continuously. But `ingest_live_session.py`
is confirmed (its own code comments, `_on_close`/reconnect handling) to
experience real connection drops and reconnect loops during live operation
("observed live: a reconnect loop every ~2s" during a rough patch, per an
existing comment in that file) — any such gap lasting more than 30 seconds
would let the `gaps` key expire, making `_is_session_live()` return `False`
for that window even though the race is very much still live.

**The failure mode this produces:** if a driver's lap-data cache entry
happens to get (re-)populated during exactly one of these transient gaps,
`get_driver_laps` applies the **86400-second** TTL instead of 30 —
freezing whatever was true at that single unlucky moment (plausibly an
early, incomplete, or even empty page) for the rest of the race, with no
further self-correction — while every other driver, whose own cache
population happened to land outside such a gap, kept refreshing normally
every 30 seconds all race. This would explain the reported symptom
precisely: nothing about VER specifically, just which driver's request(s)
happened to be unlucky enough to land inside a connectivity gap. VER being
a prominent driver people check first/often (more page loads = more chances
to be the one caught in an unlucky window) is a very plausible reason it
was VER and not someone else, without there being anything VER-specific in
the code.

**Not yet confirmed directly** — Redis was not in a state to inspect the
actual cache entries from the real race by the time this was investigated
(the stack had since restarted), so this is the leading, well-evidenced
hypothesis based on the exact documented failure mode of this exact
function, not a confirmed-via-live-inspection root cause. See below for how
a future session could confirm it more directly.

**Confirmed in the frontend, and it explains why BOTH surfaces broke
together for VER, as one bug rather than two:** `LapTimeChart` and
`SectorHeatmap` (`web/src/components/telemetry/`) both consume
`useDriverLaps`/`driverLapsQueryOptions` (`web/src/hooks/useDriverLaps.ts`)
with the **identical** react-query cache key —
`["driver", "laps", sessionId, driverId]` — explicitly so, per that file's
own comment ("Shared query options so LapTimeChart ... and SectorHeatmap
... hit the same react-query cache entry per driver instead of fetching
independently"). Both ultimately call the same `GET /drivers/{id}/laps`
endpoint, backed by the same single `get_driver_laps` Redis cache entry per
driver traced above. So a single poisoned backend cache entry for VER is
sufficient, on its own, to explain both the empty lap-times chart AND the
empty sector heatmap simultaneously — this is one root cause producing two
visible symptoms, not two separate bugs to investigate.

Also confirmed: the frontend was not at fault for failing to re-poll.
`driverLapsQueryOptions` sets `refetchInterval: 10_000` — react-query was
issuing a fresh request roughly every 10 seconds for the whole race,
including for VER. The staleness lived entirely in the **backend's** Redis
cache (serving the same poisoned response to every one of those refetches),
not in any frontend polling failure.

### Files involved

- `backend/services/driver_service.py` — `get_driver_laps`, `_is_session_live`,
  `DRIVER_LAPS_TTL_SECONDS`/`DRIVER_LAPS_LIVE_TTL_SECONDS`
- `backend/scripts/ingest_live_session.py` — `_publish_live_gaps` (the 30s
  `gaps` key `_is_session_live` depends on), the connection/reconnect
  handling (`_on_close`, the reconnect loop mentioned in this file's own
  comments)
- `web/src/hooks/useDriverLaps.ts` — `driverLapsQueryOptions` (the shared
  query key/cache both charts key off)
- `web/src/components/telemetry/LapTimeChart.tsx`,
  `web/src/components/telemetry/SectorHeatmap.tsx` — confirmed consumers of
  that same shared hook

### What needs investigating / researching before fixing

1. **Confirm this actually happened**, don't just trust the plausible
   mechanism — if the live worker/backend container logs from race day are
   still available anywhere (they likely aren't, given a restart since),
   or if Sentry captured anything relevant, check them. Otherwise, the next
   real live race is the only way to directly confirm: watch
   `f1:{season}:{round}:gaps`'s TTL during a live session and correlate any
   observed lapse with which driver's chart (if any) goes stale afterward.
2. **The real fix likely isn't `_is_session_live` itself** (its mechanism
   is reasonable) **but making `get_driver_laps` resilient to
   `_is_session_live` being transiently wrong** — options, not pre-decided:
   (a) a much shorter "unknown/live" default TTL applied whenever
   `_is_session_live` returns `False` but the session's `Race.status` isn't
   `completed` (cheap, likely sufficient); (b) don't cache an empty/near-empty
   result at all regardless of the computed TTL (an empty page is exactly
   the highest-risk case to freeze); (c) something else. Needs a real
   design decision, not just picking (a) reflexively.
3. **Audit other `_is_session_live`-gated call sites** for the same class of
   risk — this function may be reused elsewhere in `driver_service.py` or
   beyond; a transient false-negative here could plausibly affect more than
   just this one cache.
4. **Consider whether the underlying connection-drop frequency itself is
   worth improving** (separate from the caching fix) — if reconnects are
   frequent enough in real operation to matter this much, that's worth its
   own look, though this document doesn't establish how often that really
   happens in practice (the "~2s reconnect loop" comment describes one
   observed bad patch, not a general rate).

---

## 6. Cross-cutting observations (not full issues, just worth carrying forward)

- **Real DNFs this race, for reference:** LEC (1 lap), STR (26 laps), ALO
  (23 laps) all stopped short of 53 — three real retirements in one race is
  a good, realistic test case for Issue C's eventual fix and its
  verification.
- **`track_status` is entirely NULL across this whole live session** — a
  standing gap in `ingest_live_session.py`, surfaced while investigating
  Issue D but not itself chased further here.
- **The `SectorTime` table is dead code** (zero rows, ever, for any
  session) — confirmed unused by the one place that would plausibly read
  it. Not urgent, but worth a decision (finish wiring it, or remove it)
  next time anyone is in this part of the schema.
- **`isReplayActive`'s naming is misleading** (`web/src/hooks/useStrategy.ts`)
  — it's actually "is live telemetry currently flowing" (true for both a
  genuine live race and Demo Replay), not "is a Demo Replay active"
  specifically. This isn't a bug by itself, but caused real confusion while
  tracing Issue A and is worth a rename or at least a clarifying comment
  for whoever works on this file next.

---

## 7. Gap Summary

| Issue | Correct behavior | Current behavior | Confirmed root cause | Fix complexity (early read, not final) |
|---|---|---|---|---|
| A: pit window > race length | Never recommend past the last lap | Recommended lap 78 on a 53-lap race | Two layers: unbounded `optimal_pit_lap` field + `total_laps` proxy that's meaningless mid-race | Medium — needs a real `total_laps` source decision, plus a defensive clamp |
| B: undercut alert ignores race state | Alert reflects a realistic strategic threat | 100% threat with 15 laps left, no pit realistically expected | `_undercut_overcut_probability` has zero race-state/remaining-laps awareness | Medium-high — needs a real design decision on what "race state aware" means |
| C: retired driver frozen in timing tower | Retiree drops out of active standings | LEC shown all race after a lap-1 retirement | `_update_gap_state`'s eviction only fires on the exact string `"RETIRED"` | Unknown until a real feed sample is examined — could be small or could need a structural fallback |
| D: field-wide bogus lap-4 time | Implausible values excluded/flagged, not charted raw | Every driver's lap 4 shows a ~1955s "lap" | `_parse_lap_time`/`_handle_timing_data` has no plausibility check; `is_valid` hardcoded `True` for all live laps | Low-medium once the red-flag hypothesis is confirmed |
| E: VER's charts alone showed no data all race | Every driver's live chart updates identically | Only VER's lap/sector charts never updated; every other driver's did | `get_driver_laps`'s live-vs-historical TTL depends on `_is_session_live`, which can transiently read `False` during a real (documented) ingestor reconnect gap, poisoning that one cache entry with an 86400s TTL | Low-medium — the mechanism is well understood, needs a resilience fix (shorter default TTL, or never cache a near-empty result) rather than a new investigation |

None of these affect historically-ingested (post-race, `ingest_historical.py`)
sessions — all five are specific to the live path
(`ingest_live_session.py` and the live-serving branches of
`strategy_service.py`/`prediction_worker.py`/`driver_service.py`/the
frontend hooks that key off live telemetry).

---

## 8. Anchor Prompt for Resumption — paste into the new session

```
Read docs/live-race-ingestion-and-strategy-gaps-monza-2026.md in full before
doing anything else. It documents five distinct, real problems discovered
2026-09-11 by reviewing the local DB after a real live-ingested race
(Italian GP 2026, Round 13, Monza, 53 laps) that the user watched live in
the UI. Note issue D was originally written as a single issue conflating
two things, then split into D+E once the user clarified a key fact after
the first draft — read D's own correction note at the top of its section
before trusting anything else in this summary about it.

A. Pit-window recommendation exceeded the real race length (predicted lap
   78 on a 53-lap race) — two-layer bug: an unbounded StrategyPrediction.
   optimal_pit_lap field, and a total_laps proxy (MAX(lap_number) so far)
   that's meaningless while a race is still in progress.
B. An undercut-threat alert fired at 100% confidence with only 15 laps
   remaining, with no realistic chance either driver pits again —
   strategy_service._undercut_overcut_probability has no race-state or
   remaining-laps awareness at all.
C. A driver (LEC) who retired after lap 1 stayed visible in the live
   timing tower for the entire rest of the race — ingest_live_session.py's
   retirement eviction only fires on the exact string "RETIRED" in F1's
   GapToLeader field, which apparently didn't arrive for this specific
   retirement.
D. Every driver's lap-4 time was ingested as ~1955 seconds (a likely
   red-flag/session-stoppage artifact from F1's own feed), stored with no
   plausibility check and is_valid hardcoded True. Confirmed field-wide
   (every driver, not just one) — the user confirmed every driver's chart
   OTHER than VER's plotted this correctly as a real, visible spike, then
   resumed normal data — so this is real and worth fixing on data-integrity
   grounds, but it is NOT a charting bug and does NOT explain VER's own
   symptom. See E for that.
E. Only VER's lap-times/sector charts showed NOTHING at all for the whole
   race (not even the lap-4 spike everyone else showed) — every other
   driver's updated correctly, lap by lap. VER's DB data is confirmed
   complete; this is a caching bug, not a data bug. LapTimeChart and
   SectorHeatmap are confirmed (web/src/hooks/useDriverLaps.ts) to share the
   exact same query key/cache entry per driver, so one root cause explains
   both symptoms breaking together for VER specifically, not two bugs.
   driver_service.get_driver_laps applies a short 30s cache TTL only when
   _is_session_live() reads True; that check depends on a 30s-TTL Redis key
   the live ingestor refreshes continuously EXCEPT during a real,
   documented class of connection-drop/reconnect gap. If a driver's cache
   entry happens to get populated during exactly such a gap, it gets
   poisoned with an 86400s TTL instead of 30s and never self-corrects for
   the rest of the race — explaining why it was one driver, not a
   mechanism specific to VER (VER being a driver people check often is a
   plausible reason it was them and not someone else). Not yet confirmed
   via direct evidence from race day (Redis had already been reset by
   investigation time) — this is the leading, well-evidenced hypothesis,
   not a confirmed root cause.

This document is investigation-only — nothing has been fixed, and no
specific fix has been chosen for any of the five. Each issue's own section
lists concrete "what needs investigating/researching" next steps — several
require real data this session didn't have time to pull (e.g. the raw F1
feed around LEC's actual retirement, confirmation of a real red flag at
Monza 2026, direct confirmation of E's caching hypothesis from a future live
race, a direct count of how often recommended_pit_lap was null during this
real race). Do NOT skip straight to implementing a fix for any of these
without first doing that issue's own listed research — several of the
"fixes" have real, undecided design questions (what should total_laps be
sourced from? what does "race-state-aware" mean for the undercut alert?),
not just a bug to patch.

Independently re-verify this document's findings against the current
codebase and, where possible, the real DB (the Monza session's data should
still be present locally unless the stack has been reset) before proposing
anything — same rigor as every other investigation in this project
(docs/tire-deg-model-quality-and-rival-pit-behavior.md is the reference for
the expected standard of evidence: real queries, real code reads, no
assumptions stated as fact). Issue D/E's own history in this document is a
live example of why: an initial, plausible-sounding theory (D) turned out
to be wrong about the actual reported symptom once a follow-up question
from the user surfaced a fact that contradicted it — treat every finding
here as provisional in the same way until independently re-confirmed.

Propose a plan — which issue(s) to tackle, in what order, and how — and
wait for approval before implementing anything. These five issues are
independent of each other (different files, different mechanisms) and can
be picked up in any order or split across sessions; they don't need to be
fixed together. Do not run git commands unless explicitly asked.
```
