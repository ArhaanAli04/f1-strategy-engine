# Live Race Ingestion & Strategy-Feature Gaps — Italian GP 2026 (Monza)

> **Status (updated 2026-09-19): all five issues are now fixed** — A, D and E
> in earlier sessions, B and C in the 2026-09-19 five-checkpoint session (see
> each issue's own "Fix summary" and section 0c, which also lists where that
> session **corrected** claims made elsewhere in this document). The text
> below began as an investigation-only document and keeps its original
> findings and dated re-verification notes for provenance.
>
> **Where things stand at the end of the 2026-09-19 session — read this first.**
> Fixed and measured: B, C (plus A, D, E earlier). Built: the replay harness (V1),
> property tests (V4), next-race counters and raw-feed recorder (V5), and the
> **shadow-race harness (V3)**, which passed a 14-check smoke run (start to lap 12
> of 53) and, along the way, found and fixed two real production bugs in
> `prediction_worker.py` that no earlier check could see (section 7d). **Not done:**
> the full-race shadow run (V3), historical calibration of the score (V2, with V6),
> the conditional lead-lap fallback work, and anything that needs a real live race.
> A **throwaway shadow race (season 2098) is still in the local database** and must be
> cleaned up (section 7d, "State left behind"). Section 8 has the anchor prompt for the
> next session; sections 7b (what was done), 7c (what remains) and 7d (V3 in full) hold
> the detail. Nothing has been committed by the session.
>
> Original status: investigation only, NOT fixed. Discovered 2026-09-11 reviewing
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
>
> **Re-verified 2026-09-18** against the same local DB (all Monza rows still
> present) and the current source. All five issues confirmed real; four
> changed materially, and several "needs investigating" questions are now
> answered — including Issue A's `total_laps` source and Issue D's
> red-flag confirmation. **Read section 0b next** for what changed, then
> each issue's own dated re-verification note. Issue B's framing in
> particular was found to be wrong and has been re-scoped; Issue C was found
> to be blocked on evidence that no longer exists. Still no fixes attempted.

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

## 0b. Re-verification pass (2026-09-18) — what changed

Every finding above was independently re-verified against the same local
Postgres (the Monza session's rows are all still present: 1052 `lap_data`
rows, 1052 `strategy_predictions` rows, 33 `alerts` rows) and against the
current source. **All five issues are real.** Four of the five changed in
some material way, and several of this document's own "what needs
investigating" questions are now answered — including two that were listed
as needing a future live race or external research.

| Issue | Re-verification outcome |
|---|---|
| A | **Confirmed, materially worse than written.** Layer 2's frequency claim is now measured, not reasoned: 94.6% null. Also: the open `total_laps` design question now has an authoritative answer (see A.1 below). |
| B | **Confirmed, but the framing was wrong.** The real defect is score *bistability*, not primarily race-state blindness. The specific alert cited (ANT/VER at lap 38) is not what the DB shows. |
| C | **Confirmed. The #1 research step is impossible** — no raw feed sample exists or can be recovered. |
| D | **Confirmed and extended.** The anomaly spans laps 3-6, not just lap 4; the red-flag hypothesis is now confirmed from the DB alone; and the signal needed to fix it is *already being received and discarded*. |
| E | **Mechanism upgraded from hypothesis to documented-recurring**, via a precedent found in this codebase's own comments. One research question closed for free. |

Each issue's section below carries its own dated re-verification note with
the real numbers. Where a number in the original text is superseded, the
note says so explicitly rather than editing the original claim away — same
provenance convention this project's CLAUDE.md uses.

**One caveat that applies to A and B together, and was not available to the
original investigation:** this race ran **2026-09-06**, which is *before*
the 2026-09-11 CP1-CP3 tire-deg/pit_predictor retrain
(`docs/tire-deg-model-quality-and-rival-pit-behavior.md`). Every model
output captured in this session's `strategy_predictions` rows — including
the `tire_life_remaining = 40.00` pegging that drives Issue A's arithmetic
— comes from the **pre-retrain** models. The structural bugs are real and
independent of model quality, but the observed *magnitudes* should not be
assumed to still hold on current models without re-measuring.

---

## 0c. Fix session (2026-09-19) — Issues B and C fixed, and what it corrected

Issues B and C were fixed in five checkpoints (CP1-CP5), each with its own
tests and checks; the per-issue "Fix summary" sections hold the detail. This
section records what the session established about the evidence itself,
including where it **contradicts** earlier text in this document.

**The evidence source that made this possible.** Issue C's re-verification
note said the raw feed sample for LEC's retirement "cannot be done" — that no
recording exists or can be recovered. That was wrong. `ingest_live_session.py`
keeps no raw messages, but F1 publishes the same per-session streams
(`TimingData.jsonStream`, `TimingAppData`, `TrackStatus`, `DriverList`, ... —
the files FastF1 reads) and for Monza 2026 R they are retrievable read-only
(`fastf1._api.fetch_page`; 53,795 `TimingData` messages). Everything below
about what F1 actually sent comes from that archive.

### Corrections to earlier text

| Earlier claim (where) | What the evidence showed |
|---|---|
| F1 signals a retirement with the string `"RETIRED"` in `GapToLeader`; the eviction was "validated against a real session with 3 retirements" (`ingest_live_session.py` docstring, this document's Issue C) | That string appears **nowhere** in Monza's 53,795 messages. F1 sends booleans on the car's own entry: `Retired`, `ShowPosition`, `Stopped`. The earlier "validation" ran `verify_live_feed_parity.py`, which **synthesized** the `"RETIRED"` marker itself from database rows — it validated the harness's own assumption, not F1's feed. |
| Only one car (LEC) froze in the tower (Issue C) | Three retirees and four lapped cars. LEC: `Stopped` true/false/true/false, then `Retired`+`Stopped`, then `ShowPosition=false` ~27 min later. ALO: `Retired` then `ShowPosition=false`. STR: `Stopped` then `ShowPosition=false` and **never** `Retired`. Lapped cars (BOT, PER, ALB, OCO) send `"1 L"`, `"1L"`, `"52L"`, which the ingestor's `LAPS?`-only pattern never matched, so their last numeric gap was held for the rest of the race. |
| F1 sends `Position` only once, in the Subscribe snapshot (`ingest_live_session.py`, from the 2026 Dutch GP live run) | In the archived stream `Position` arrives 14-44 times per car and forms a valid 1..N ranking at **1052 of 1052** lap completions. The archive is what F1 recorded server-side, not proof of what the live socket delivers, so the gap-based ranking was kept as a fallback (see Issue C's fix summary). |
| Issue B's primary defect is score *bistability* — the deterministic tyre delta swamping the noise term (this document's Issue B "Correction 2") | The swings tracked the **starting gap** fed into the calculation, which was wrong (Issue B's fix summary). Once it was corrected the same model gives mostly in-between scores when the gap is under 4s; what looked like saturation is largely a legitimate near-zero for gaps too large to close in a lap. |

### Verification, all measured
Monza is the only live-ingested full race in the database, so every
before/after number below is on that one race. Unit suite: 429 at the start
of the session → 458 (CP1) → 481 (CP2) → 501 (CP3) → 513 (CP4) → 539 (CP5) → 564 (V4, V1) → 568 (gap-only fallback fix) → 607 (V5), always with
`mypy --strict` and `ruff` clean repo-wide; integration tests (real Postgres
and Redis) 4 → 16 → 23 passed across CP2, CP3 and CP5.

### What this session did NOT establish
- **No genuinely live race has run with these fixes.** The evidence is a
  replay of F1's archived feed through the unmodified ingestor, plus tests.
- **The archive is not the live socket.** Whether the live connection streams
  `Position` is only observable at the next live race — the ingestor flips a
  flag (`_position_diff_seen`) when it does.
- **Existing Monza rows are not corrected** — stored `lap_data.position`,
  `strategy_predictions` and alerts are as they were. CP4 re-scored them
  offline only.
- **Models changed since race day** (the 2026-09-11 retrain), so CP4's
  stored-vs-rescored comparison mixes a model change with the gap change; its
  old-gaps-vs-live-gaps comparison holds models constant and isolates the gap.

---

## 1. Issue A — Pit-window recommendation exceeded the real race length (predicted lap 78 on a 53-lap race) — ✅ FIXED 2026-09-19

> **✅ Fixed 2026-09-19, four checkpoints.** `Session.total_laps` now
> stores the real scheduled race distance (migration
> `20260918_add_total_laps_to_sessions`), populated historically from
> FastF1's own `session.total_laps` and live from the feed's `LapCount`
> topic (previously subscribed to nothing that carried this). Every real
> consumer of the old `MAX(lap_number)`-so-far proxy — including a THIRD
> instance the original investigation didn't find, in
> `get_competitor_predicted_strategy` — now prefers the real value, and
> `optimal_pit_lap` is clamped to it once known. See "Fix summary
> (2026-09-19)" at the end of this section for the full implementation,
> the real-data verification (all 155 backfillable completed R sessions
> confirmed populated, live-endpoint checks against real Belgian GP data),
> and a genuine pre-existing test bug found and fixed along the way
> (unrelated to Issue A, confirmed via `git stash` against the
> pre-this-session code).

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

### Re-verified 2026-09-18 — measured, and worse than written

All 1052 `strategy_predictions` rows for this session were queried directly.

**Layer 1 confirmed, with a literal match rather than circumstantial
arithmetic.** The original text hedged that "whether the driver in question
was actually at lap 38 specifically wasn't independently re-confirmed." It
now is — and it wasn't one driver, it was the whole field:

```
SELECT d.code, sp.lap_number, sp.optimal_pit_lap, sp.recommended_pit_lap, sp.tire_life_remaining
  FROM strategy_predictions sp JOIN drivers d ON d.id = sp.driver_id
 WHERE sp.session_id = '3ddc84bd-f10e-4870-9e98-631d79695beb' AND sp.lap_number = 38;
```

returns **19 rows, every single one with `optimal_pit_lap = 78` and
`tire_life_remaining = 40.00`** (the `MAX_LOOKAHEAD_LAPS` peg), and 17 of
the 19 with `recommended_pit_lap` NULL (only BOT and PER got 39). So "Lap
78" was on screen for essentially any driver the user clicked at that
moment, not a one-off.

Session-wide bounds:

| Measure | Value |
|---|---|
| `optimal_pit_lap` range | **41 to 93** (race is 53 laps) |
| rows with `optimal_pit_lap > 53` | **778 / 1052 = 74.0%** |
| rows with `recommended_pit_lap` NULL | **995 / 1052 = 94.6%** |
| rows both NULL-recommendation *and* beyond-race-length | 721 |
| `recommended_pit_lap` / `window_end` max | **53 / 53** — 0 rows beyond race length |

That last row is the clean confirmation of Layer 1 as originally stated:
the capped field is genuinely capped and never misbehaved; **only** the
unbounded `optimal_pit_lap` escapes.

**Layer 2's frequency claim is now measured, and the mechanism is proven,
not inferred.** The original text explicitly flagged this as "reasoned from
the code, not independently measured." Computing, for every row, the
headroom `field_max_lap_at_predict_time − that driver's own lap_number`
(where `field_max_lap_at_predict_time` is `MAX(lap_data.lap_number)` among
rows whose `created_at <= sp.predicted_at`):

| `recommended_pit_lap` | rows | min headroom | avg headroom | max headroom |
|---|---|---|---|---|
| NULL | 995 | −1 | **0.00** | **0** |
| present | 57 | **1** | 1.05 | 2 |

**Perfect separation across all 1052 rows**, with no overlap. A
recommendation exists if and only if the field had already moved at least
one lap past the lap being predicted for — i.e. only when the Celery worker
was running *behind* the race. Null-rate by lap bucket confirms the same
shape from the other direction: laps 1-19 are **100% null** (0 of 400),
rising only to 23 of 190 in the lap 40-49 bucket as worker backlog grows.

**This is a stronger conclusion than the original section drew.** Even in
the 57 rows where a recommendation *did* appear, max headroom is 2 — so
`pit_laps = np.arange(current_lap + 1, max_pit_lap + 1)` was searching a
**1-2 lap window, never the intended 15**. The pit-window recommendation
engine is not "degrading to a fallback occasionally" during a live race; it
is **functionally dead for the entire race**, and on the rare occasions it
produces a number at all, that number is the output of a search over one or
two candidate laps. Fixing the unbounded-clamp alone (Layer 1) would
therefore replace a wrong number with a *capped* wrong number — it does not
restore the feature.

### `total_laps` has an authoritative source — research question 2 is answered

The original research question 2 listed only speculative options ("(a) add
a real `total_laps` column, populated from Ergast/FastF1's own
scheduled-laps data — needs a data source audit, does Ergast reliably carry
this pre-race?; (b) a live-only fallback heuristic; (c) something else").
Verified answer: **F1's own live timing feed broadcasts it directly**, and
this project is simply not subscribed to it.

- `LapCount` is a real live-timing topic — FastF1's own reference SignalR
  client subscribes to it (`fastf1/livetiming/client.py:103`, alongside
  `TopThree`/`RcmSeries`), and it is archived per session as
  `LapCount.jsonStream` (`fastf1/_api.py:53`, "Lap counter").
- Its `TotalLaps` field is explicitly **the originally scheduled lap
  count** — FastF1's `Session._load_total_lap_count` (`fastf1/core.py`)
  reads exactly this to populate its own public `session.total_laps`
  property, with the comment *"'TotalLaps' is intended lap count, use last
  value that is not None in case of wrong data being corrected later.
  Shouldn't usually change."*
- `ingest_live_session.py`'s `_TOPICS` (line 70) does **not** include
  `LapCount`.

So both ingestion paths have a real, authoritative source with no heuristic
and no Ergast dependency: **live** → subscribe to `LapCount`, read
`TotalLaps`; **historical** → FastF1's `session.total_laps`. What remains a
genuine open decision is only *where to store it* (a `total_laps` column on
`sessions` vs. on `races` — `sessions` looks right, since a race's R/Q/FP
sessions have different lap counts and the column is a session property),
and the fallback behaviour when the feed never sends it.

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

1. ~~**Independently confirm Layer 2's frequency claim with real data**~~ —
   **✅ answered 2026-09-18, see the re-verification section above.** 94.6%
   null, and the mechanism proved out with perfect separation on all 1052
   rows. The follow-on finding (the search window is only ever 1-2 laps
   wide even when it *does* return a value, so a clamp alone doesn't restore
   the feature) is new and should drive the fix's scope.
2. ~~**Design a real `total_laps` source.**~~ — **✅ source identified
   2026-09-18 (`LapCount.TotalLaps` live / `session.total_laps` historical,
   see above); only the storage location and no-feed fallback remain open.**
   Original framing kept below for context: CLAUDE.md already documents that
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
3. ~~**Decide whether `optimal_pit_lap`/`predicted_pit_lap` should be
   clamped independently**~~ — **✅ done 2026-09-19 (CP4).** Clamped to
   `Session.total_laps` specifically when known — see the fix summary
   below for why the naive version of this (clamping against
   `resolved["total_laps"]`, which is real-value-OR-still-the-old-proxy)
   was a real mistake caught during implementation, not shipped.
4. **`isReplayActive`'s naming/semantics** — not renamed as part of this
   fix; still genuinely open, tracked in the Cross-cutting Observations
   section below rather than re-litigated here.

### Fix summary (2026-09-19)

Four checkpoints, approved and implemented in sequence.

- **CP1 (schema):** `Session.total_laps: int | None`, migration
  `20260918_add_total_laps_to_sessions` — nullable, non-regressive by
  construction. Generated and applied via the project's `/migrate` skill
  (steps 1-8; commit deliberately skipped per instruction). `alembic
  check` confirmed zero drift between the model and the migration.
- **CP2 (write path):**
  - `_ingest_common.get_or_create_session` gained an optional `total_laps`
    param: sets it on a new row, backfills an existing NULL row, **never**
    overwrites an existing value.
  - `ingest_historical.py` passes `fastf1_session.total_laps` through
    (already loaded via its existing `load(laps=True, ...)` call).
  - `ingest_live_session.py` subscribed to the `LapCount` topic (was in
    `_TOPICS` for no functional reason before this — no handler existed)
    and added `_handle_lap_count`, dispatching a new
    `update_session_total_laps` Celery task (`telemetry_worker.py`) once
    per distinct value seen.
  - `backend/scripts/backfill_session_total_laps.py` (new, `make
    backfill-session-total-laps`): backfills completed R sessions from
    `MAX(lap_number)` — correct by construction for a genuinely finished
    race, no FastF1 fetch needed. Deliberately scoped to `Race.status ==
    "completed"` only, per this section's own research-question-2
    decision — a partially-live-ingested session (Monza itself) is left
    `NULL` rather than backfilled with a value indistinguishable from a
    real one. **Run for real against the local DB: 155 of 158 R sessions
    backfilled** (the 3 not backfilled: Monza itself, still
    `status="scheduled"` as designed, plus 2 completed races with zero
    `lap_data` rows — one handled gracefully with a warning, the other
    likewise).
- **CP3 (read path):** `strategy_service._current_state` and
  `prediction_worker._resolve_inference_context` now prefer the stored
  value, falling back to the old `MAX(lap_number)` proxy only when it's
  genuinely unknown. **A third, previously-undiscovered instance of the
  identical bug was found and fixed in the same checkpoint:**
  `strategy_service.get_competitor_predicted_strategy`'s own `total_laps =
  max(lap.lap_number for lap in latest_laps)` — the same "how far has the
  race gotten" proxy, computed in Python instead of SQL, feeding
  `_first_pit_laps_over_threshold_batch`'s horizon calculation directly
  and silently shrinking every competitor's pit-lap search horizon to 1
  lap mid-race. This backs `/strategy/{session_id}/overview` — CLAUDE.md's
  own "single most compute-expensive endpoint" — not a minor code path.
  This also closes the layer-1 corruption the original investigation
  flagged but didn't fully trace: `total_laps` collapsing toward
  `current_lap` mid-race was feeding wrong values into three ML features
  (`fuel_load_penalty`, `fuel_load_est`, `laps_to_race_end`) on every
  single live prediction, not just the displayed pit lap — the visible
  "Lap 78" was the symptom, this was the larger defect underneath.
- **CP4 (clamp + frontend):** `_run_inference`'s `optimal_pit_lap` is
  clamped to the real total_laps once known. **A real design mistake was
  caught mid-implementation, not shipped:** the first draft clamped
  against `resolved["total_laps"]` — which is *also* true for the
  meaningless mid-race proxy value, not just a genuinely-known real one.
  That would have silently replaced one wrong number (obviously
  implausible, e.g. lap 78 of 53) with a *different* wrong number that
  looks plausible (≈ current lap, since the proxy tracks progress) —
  exactly the trap this section's own research-question-3 discussion
  warned against. Fixed by threading a separate `stored_total_laps` field
  (the real value specifically, `None` otherwise) through
  `_resolve_inference_context`'s return and clamping against *that*, not
  the coalesced value — caught by a fixture in the existing integration
  test suite (`test_live_prediction_pipeline.py`, proxy=40) before it
  reached any real verification. On the frontend,
  `web/src/hooks/useStrategy.ts`'s `PitRecommendationView` gained
  `isFallbackEstimate: boolean` (true only when
  `viewFromHistoryEntry` falls back to the raw, potentially-still-unbounded
  `predicted_pit_lap` — a pre-fix persisted row is never retroactively
  corrected, and a live session before its first `LapCount` message
  legitimately still has no real total_laps to clamp against). The
  frontend has no race-length context of its own to sanity-check the
  number, so `PitWindowCard.tsx` surfaces the uncertainty honestly instead
  (a `~` headline prefix, "Estimated" instead of "Recommended," an
  "Unconfirmed estimate" caption) rather than attempting a client-side
  clamp. Desktop/mobile were checked directly and confirmed **not**
  affected — both already, deliberately, only ever render the REST
  `/pit-window` source (always bounded), never the history-based fallback.

**Verified, not just implemented:**
- `ruff`/`ruff format`/`mypy --strict` clean on every changed file across
  all four checkpoints; `tsc -b`/`oxlint` clean on the frontend changes.
- New/updated unit tests at every checkpoint (schema-adjacent
  `get_or_create_session` tests, `_handle_lap_count`/Celery task tests,
  `_current_state`/`_resolve_inference_context`/
  `get_competitor_predicted_strategy` prefer-vs-fallback tests with
  captured-argument assertions, `_run_inference` clamp tests using the
  exact real Monza numbers, frontend `isFallbackEstimate` rendering
  tests). Full backend unit suite: **429 passed, 0 failed** by CP4
  (341 baseline + 88 new across the whole fix); full web vitest suite:
  **56 passed, 0 failed**.
- **Real integration-test runs against a genuine testcontainer Postgres**
  at both CP3 (11 passed) and CP4 (4 passed after the bug below was
  fixed) — not just mocks.
- **Real-DB/live-API verification**: hit the real running
  `/strategy/{session_id}/overview` endpoint against Belgian GP R10 (now
  carrying a real backfilled `total_laps=44`) — responds correctly.
  Restarted the worker container (Celery doesn't hot-reload) and
  confirmed clean startup with `update_session_total_laps` correctly
  registered — no import errors.
- **A genuine pre-existing test bug found and fixed, unrelated to Issue
  A:** `test_resilience.py::test_prediction_worker_continues_on_model_exception`
  asserted `tire_life_remaining == 0.0` on a model-exception fallback, but
  `_run_inference`'s real, documented, long-standing fallback is
  `tire_deg_model.MAX_LOOKAHEAD_LAPS` (40.0) — confirmed via `git stash`
  that this assertion failed identically against the code as it stood
  *before* any of Issue A's fixes, so this predates and is unrelated to
  this session's work. Fixed the test's expected values (`40.0`/
  `lap_number + 40`) to match real production behavior, not the other way
  around. Not currently wired into any CI workflow (`resilience` isn't a
  marker any `.github/workflows/*.yml` selects) — a separate, real gap,
  noted here rather than silently left.

**Honest limitation:** no live race occurred during this fix. The
real-data checks above (backfill against 155 real historical sessions,
live-endpoint checks, exact-Monza-number unit tests) are the strongest
verification available without one — they are not a substitute for
watching a genuinely live race with the fix in place.

---

## 2. Issue B — Undercut threat alert ignored overall race state — ✅ FIXED 2026-09-19

> **✅ Fixed 2026-09-19 (CP3-CP5).** The real defect was not race-state
> blindness or score bistability as first written: a live session's undercut
> gap came from summed lap times that omit lap 1 for every driver, and the
> rival was chosen from stored positions that still contained retired cars.
> Live sessions now take both from F1's own live standings, and the alert
> layer was fixed separately (its own defect, found while measuring). See
> "Fix summary (2026-09-19)" at the end of this section, and section 0c for
> the corrections it makes to this section's earlier text.

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

### Re-verified 2026-09-18 — real, but this section's framing is wrong

The **alerts are real and were persisted**: 33 `UNDERCUT_THREAT` rows exist
for this session, spanning `13:05:06` to `14:40:16` UTC — i.e. the whole
race, right up to the closing laps. The code reads exactly as described
(`_undercut_overcut_probability` takes no race-state input;
`evaluate_threats` gates only on `score > UNDERCUT_ALERT_THRESHOLD = 0.5`).
So the feature-level complaint stands. Two corrections, though, one of them
significant.

**Correction 1 — the specific alert cited above is not what the DB shows.**
The real late-race alert messages are:

```
14:40:16  VER  Undercut threat: VER on PIA (55%)
14:39:12  VER  Undercut threat: VER on PIA (55%)
14:38:01  VER  Undercut threat: VER on PIA (55%)
14:23:34  VER  Undercut threat: VER on GAS (100%)
14:22:26  VER  Undercut threat: VER on GAS (100%)
14:10:11  ALO  Undercut threat: ALO on PER (100%)
```

There is no ANT/VER alert. The **100%** alerts were `VER on GAS`, and
correlating `predicted_at` back to `lap_number` puts them at **lap 30 of
53** — not lap 38, and not "15 laps remaining." The alerts that *were*
firing at ~15 laps to go read **55%**, comfortably above the 0.5 threshold
but not the "100% confidence" the section describes. The user's recollection
of the pairing and the lap was approximate; the underlying observation (a
100% undercut alert that didn't reflect a realistic threat) is still real.

**Correction 2 — and this is the important one: the primary defect is score
*bistability*, not race-state blindness.** Lap 30 of 53 is a perfectly
ordinary pit window — "there's no realistic chance either car pits again"
simply does not describe the moment the 100% alert actually fired. What the
data does show is that the score is numerically unstable lap to lap. VER's
`undercut_score` across consecutive laps:

```
lap 29  0.070      lap 34  0.000      lap 39  0.000
lap 30  1.000  ←   lap 35  0.000      lap 40  0.000
lap 31  0.010      lap 36  0.035      lap 41  0.550
lap 32  0.005      lap 37  0.200      lap 42  0.550
lap 33  0.000      lap 38  0.465      lap 43  0.000
```

A jump from 0.070 to **1.000** and back to 0.010 on three consecutive laps
is not a credible probability estimate under *any* race-state policy.
Session-wide, the distribution is heavily saturated rather than spread:
**131 of 1052 rows sit at ≥0.999 and 547 at ≤0.001** — 64% of all rows are
pinned at one extreme or the other, with only ~36% anywhere in between.
297 rows exceed the 0.5 alert threshold.

**Why this changes the fix.** The originally-proposed remedies (a
remaining-laps cutoff, a confidence discount that scales with laps
remaining, a `pit_probability` cross-check) would all have suppressed the
*late* alerts while leaving the lap-30 100%-then-1% swing completely
untouched — because that one fires in the middle of the race, where every
proposed race-state gate would pass it. Any real fix has to address
calibration/stability first; a race-state gate is a reasonable second layer
on top, not the fix.

Worth noting what this is **not**: CLAUDE.md documents a separate
saturation cause in `_resolve_position_context` (no `lap_number <=
current_lap` bound, producing frozen race-end-anchored scores). That
mechanism is explicitly scoped to *replaying a fully-ingested historical
session* — Monza was genuinely live-ingested lap by lap, so "latest lap"
tracked the real current lap throughout and that bug should not bite here.
The saturation seen above therefore appears intrinsic to
`_undercut_overcut_probability`'s own deterministic-delta-vs-noise balance
(the deterministic stint-delta difference swamping
`_sampled_noise`'s spread, driving the win fraction to 0 or 200 of 200),
not inherited from the position-context bug. **This was reasoned from the
two mechanisms' scopes, not measured** — quantifying the delta/noise ratio
directly is the right first step of a future fix session.

### Files involved

- `backend/services/strategy_service.py` — `_undercut_overcut_probability`,
  `UNDERCUT_PROJECTION_LAPS`/`UNDERCUT_MONTE_CARLO_SIMS` constants,
  `_sampled_noise` (the noise term whose scale relative to the
  deterministic delta is the likely saturation driver)
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

### Root cause, re-established 2026-09-19 (supersedes "Correction 2" above)

Reading the score against the inputs it was fed, VER's `undercut_score`
tracked the starting gap almost exactly: lap 30 (target GAS) had a summed-time
deficit of **−0.99s** (VER "ahead") and scored 1.000; lap 33 (target ANT)
+7.85s scored 0.000; lap 37-38 (target PIA) +1.59s/+0.98s scored 0.200/0.465.
That gap was wrong for a live session:

- `_cumulative_race_time` falls back to `SUM(lap_time_seconds)` (live rows
  have no `session_elapsed_seconds`), and **lap 1 has no recorded time for
  any of the 22 drivers**, so every gap that opened on lap 1 is missing.
  The red-flag laps (lap 4, ~1949-1958s, different for each driver) also feed
  the sums with up to ~10s of cross-driver spread.
- Over the 784 predictions that had a car ahead, the summed gap said the
  requester was already ahead of it in **258 (33%)**, against **33 (4%)** by
  the actual lap-crossing timestamps; median absolute error 2.56s. 66 of the
  109 scores at >= 0.999 and 142 of the 245 above 0.5 sat on such a
  wrong-signed gap.
- The *rival* was also wrong: it came from stored `lap_data.position`, where
  a car retired on lap 1 (LEC) stayed in the field for the whole race — see
  Issue C.

### Fix summary (2026-09-19)

- **CP3 — gap and target from F1's live standings.**
  `strategy_service._live_gap_deficit` reads `f1:{season}:{round}:gaps` and
  returns the difference of the two drivers' gap-to-leader (leader = 0). It
  only trusts a payload with `source == "live"` and a `session_id` matching
  the session being evaluated (the key is per season/round, and a replay
  writes to it too). It declines — and the old summed-time deficit is kept —
  when either driver is absent (retired) or lapped, since a lapped car has no
  seconds gap to the leader. `_undercut_overcut_probability` gained a `client`
  argument and uses the live deficit when present; `get_undercut_score` /
  `get_overcut_score` pass it through. `strategy_service._resolve_field_neighbors`
  and `prediction_worker._resolve_position_context` resolve a live session from
  the same standings *first* (target and `pit_predictor`'s gap features from
  one source); their existing DB-then-Redis fallback is unchanged for replays
  and historical sessions. The snapshot is the current tower, not the tower
  as of the lap being predicted; on Monza the worker was level with the race
  on 95% of predictions.
- **CP4 — measured offline** (`backend/scripts/evaluate_undercut_live_gaps.py`,
  read-only): all 1052 stored predictions re-scored on current models, old
  gap/target vs live gap/target, same random draws.

  | Measure | Old gaps | Live gaps |
  |---|---|---|
  | Requester "already ahead" of its target | 29.9% (299/999) | 0.5% (5/942) |
  | Median error vs on-road crossing gap | 2.15s | 0.28s |
  | Target was a car that had already stopped | 81 (LEC 53, ALO 28) | 2 |
  | Scores >= 0.999 | 185 | 74 |
  | Alert-eligible (> 0.5) | 338 | 202 |
  | Lap-to-lap swings >= 0.9 | 57 | 37 |

  VER lap 30: stored 1.000; old gaps on current models 0.665 (deficit
  −1.00s); live gaps **0.220** (deficit +0.22s). The target changed on 182 of
  999 comparable rows; 57 rows had no live answer and fell back to the summed
  time. With live gaps, most scores when the gap is under 4s are in-between
  (67%); 90% of scores at gaps over 4s are near zero, which is a legitimate
  answer, so the earlier "64% pinned at 0 or 1" was not the defect it looked.
- **CP5 — the alert layer had its own defect, and two gates.**
  `alert_service.evaluate_threats` built its adjacent pairs from each
  driver's latest stored lap row, which keeps a retiree in the order at its
  last recorded position for the rest of the race, and it alerted the
  trailing driver's own score against whichever car happened to be adjacent.
  In the CP4 replay with corrected scores this produced `VER on LEC` ×16 of 44
  alerts. It now takes the running order from the live standings
  (`_live_standing_order`, same live-and-same-session rule as above) and
  keeps the old order for replays and historical sessions. Two gates
  (`_alert_suppressed_by_race_state`) suppress the *alert* — the stored
  `undercut_score` is unchanged — when the trailing driver's tyres are 3 laps
  old or less (`UNDERCUT_ALERT_MIN_TYRE_AGE_LAPS = 4`: 45 of the 202
  alert-eligible predictions) or fewer than 15 laps remain
  (`UNDERCUT_ALERT_MIN_LAPS_REMAINING = 15`: 25 of them; only applied when the
  real distance, `Session.total_laps`, is known, never the laps-so-far proxy).
  The gates run before the subscriber lookup and the dedup claim so a
  suppressed alert does not use up the pair's 60s claim.

  Alert replay (single subscriber, VER + ALO, current models, live gaps):

  | Alert pairing | Alerts | Involving an already-stopped car |
  |---|---|---|
  | as sent on race day (replayed; the race sent 33) | 32 | 2 |
  | live gaps, old alert pairing | 44 | 16 |
  | + live-standings pairing | 46 | 2 |
  | + both gates | **29** | 1 |

  The replay reproduces 32 of the 33 real alerts (per-pair counts match for
  ALO on PER, VER on PIA, VER on LEC, VER on GAS; the gap is ALO's early alerts
  on ALB/BOT/STR, 11 vs 12, from ties in early-race stored positions).
  Tyre-age alone takes 46 to 35, laps-remaining alone 46 to 40; neighbouring
  thresholds give 26-35 alerts, so the choice is not on a knife edge. The
  1-2 remaining "stopped car" alerts are from before F1 itself flagged LEC as
  stopped.

### Not done / limits
- The remaining-laps gate needs `Session.total_laps`, filled live from F1's lap
  count (Issue A). **Monza's own row is NULL**, so that gate would not have run
  on Monza; the replay above treats it as 53. F1's feed carries `TotalLaps: 53`.
- A driver with no stored lap row is not suppressed (nothing to judge by).
- Replays and historical sessions still form the alert pairing from stored
  rows (retirees linger). Left as is.
- The alert target is not stored with the prediction: the alert pairs the
  trailing car with the live-adjacent one, which is the same source the worker
  used a moment earlier, but if the order changes in between they could differ.
- The remaining alerts (`VER on RUS` ×12, `VER on ANT` ×7 in the final replay)
  are on genuine adjacent pairs; whether they were *right* cannot be judged
  without ground truth.
- The 0.5 alert threshold, the two gate constants and the 60s dedup were not
  re-derived from data beyond the sensitivity check above.

---

## 3. Issue C — Retired driver (LEC) never left the timing tower — ✅ FIXED 2026-09-19

> **✅ Fixed 2026-09-19 (CP1-CP2).** The "research step 1 is impossible" note
> below was wrong: F1's archived per-session feed was retrievable and showed
> what F1 really sends for a retirement — three boolean fields, not a
> `"RETIRED"` string — and that lapped cars were frozen the same way. See
> "Archive findings and fix summary (2026-09-19)" at the end of this section.

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

### Re-verified 2026-09-18 — confirmed, but research step 1 is impossible

The code is exactly as described: `_RETIRED_MARKER = "RETIRED"` (line 146),
and `_update_gap_state` (line 623) evicts only on
`gap_to_leader_raw.strip().upper() == _RETIRED_MARKER`. LEC's DB footprint
re-confirmed: **1 row**, `lap_number=1`, `position=3`. The other two real
DNFs are also confirmed (STR 26 laps, ALO 23 laps) — all three stop short
of the field's 53.

**Research step 1 ("get a real recorded feed sample of LEC's actual
retirement") cannot be done.** Checked directly:

- `ingest_live_session.py` never persists raw feed messages anywhere — it
  parses each message and discards it. There is no raw-message log, no
  `.jsonStream` capture, no replay dump.
- `FASTF1_CACHE_DIR` (`/tmp/fastf1_cache`) is FastF1's own HTTP cache for
  its *historical* API, not a live-feed recorder — it holds nothing from
  this live SignalR session.
- Race-day container logs are gone; the compose stack has been recreated
  since (containers are minutes old at re-verification time).

So there is no path to seeing what F1 actually sent for LEC. **This changes
the fix's sequencing rather than its substance**: either ship a
marker-independent backstop on reasoning alone (risky — a staleness-based
eviction heuristic tuned without ground truth can evict healthy cars, e.g.
a car genuinely stationary in a long pit stop or under a red flag, which
this very race had), or add raw-feed recording behind a flag *first* so the
next live race produces the evidence, then fix against real data. The
latter is slower but is the only option that can actually answer research
step 2 ("what signals real F1 retirements can look like") rather than
guessing at it.

Research step 3 (did STR/ALO show the same frozen-tower symptom?) is
likewise unanswerable retroactively — it depends on what the UI displayed,
which was never captured — but it is worth explicitly watching for at the
next live race with a retirement, since "only LEC" vs. "all three" still
discriminates sharply between a feed-specific quirk and a broken mechanism.

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

### Archive findings and fix summary (2026-09-19)

**What F1 actually sends** (Monza 2026 R archive, `TimingData`, 53,795
messages; stream time h:mm:ss):

| Car | Signals |
|---|---|
| LEC (16) | `Stopped` true 1:00:58 → false 1:11:56 → true 1:12:32 → false 1:12:38; `Retired`+`Stopped` true 1:26:29; `ShowPosition` false 1:53:33. Last numeric gap `+1.759` at 1:11:56, then only `""` and `"1 L"`. |
| ALO (14) | `Retired`+`Stopped` 2:07:58; `ShowPosition` false 2:10:42 |
| STR (18) | `Stopped` 2:11:58; `ShowPosition` false 2:15:31; **never `Retired`** |

The string `"RETIRED"` does not occur in any `GapToLeader` or
`IntervalToPositionAhead` value. `Stopped` was only ever true for those three
retirees (107 `InPit` events, none with `Stopped`). Lapped cars send `"1 L"`,
`"1L"`, `"52L"` (and the leader's own counter `"LAP 14"`, which is not a
lapped car).

**Why the tower broke.** The ingestor ranks cars by `GapToLeader`, so a car
whose gap stops updating keeps its last value: LEC held `+1.759` (P2-P3) for
the rest of the race, shifting everyone behind it by one place. Lapped cars
were frozen the same way, because `_LAPS_BEHIND_PATTERN` required the word
`LAP`/`LAPS`.

**Baseline** (CP1: a replay harness, `backend/scripts/verify_live_feed_archive.py`,
that drives the unmodified ingestor with F1's recorded messages and audits what
it publishes against F1's own per-car state; no database, Redis or Celery):

- lap-completion position matched F1's own `Position` field on **117 of 1052
  (11.1%)**; the published tower's rank was wrong for 82.7% of active-car
  samples (+1 offset on 702,551 of them, +2 on 141,105, +4 on 27,012);
- LEC, ALO and STR remained published to the end of the race;
- stale numeric gaps held for lapped cars: BOT 23,225, PER 23,018, ALB 6,106,
  OCO 2,620 published samples;
- after dropping ghost cars and re-ranking, 11.1% were still wrong — the
  lapped-car freeze — so fixing retirements alone would not have been enough.

**Fix (CP2, `ingest_live_session.py`):**
- A car is out of the ranking and the published standings when any of
  `Retired`, `ShowPosition == false` or `Stopped` is set (`_is_out_of_ranking`).
  Its gap state is kept, so a car that recovers from a stop (LEC restarted
  twice) rejoins with its history intact. The dead `"RETIRED"` check was removed.
- Lapped strings are parsed (`"1 L"`, `"52L"`, ...) into a `laps_down` count and
  such cars rank behind every lead-lap car, ordered by laps down.
- The ranking uses F1's own `Position` field once a `Position` update has been
  seen on a live feed diff (`_position_diff_seen`; the Subscribe snapshot does
  not count), only while every ranked car has a distinct value, and otherwise
  falls back to the gap-based ranking. Retired cars are dropped first so no
  hole is left.
- `verify_live_feed_parity.py` now sends the real Retired/Stopped/ShowPosition
  booleans instead of the invented string.

**Result** (same archive, full race): lap-completion position match **100.0%**
(1052/1052) with `Position` streaming and **94.1%** on the gap-only fallback
(`--no-position-diffs`); ghost cars and stale lapped gaps **0** in both;
0 swallowed handler errors. The older DB-driven harness is unchanged at
99.0% position / 94.1% tyre age. The gap-only path's remaining misses are the
inherent noise of gap-based ranking around pit stops.

**Tests:** 29 harness tests (CP1, including a 31 KB excerpt of real F1 messages
committed as `backend/tests/unit/fixtures/monza_2026_r13_timing_excerpt.json`
so the fix is pinned on real messages, in CI, without network), then CP2
replaced the four tests that pinned the invented `"RETIRED"` string with tests
of the real signals, lapped-car parsing and the Position-vs-gap ranking choice
in `test_ingest_live_session.py`, plus real-message regressions (both ranking
modes) in the harness tests.

**Not covered:** whether the live socket delivers `Position` (the archive is
what F1 recorded, and the 2026 Dutch GP run saw it only in the snapshot — the
fallback exists for that reason; the first live race will show which path runs);
existing Monza `lap_data.position` rows keep their pre-fix values.

### Multi-race replay (V1, 2026-09-19) — and a weakness it found in the gap-only fallback

Monza was the only race the fix had been measured on. The replay harness
(`--rounds 1-14`) then replayed all 14 completed 2026 races, each twice: with
F1's `Position` field streaming, and gap-only (`Position`/`Line` stripped from
every replayed diff — the fallback that runs if the live socket does not stream
`Position`). Each race's snapshot boundary is detected from its own archive.
`ret` = cars F1 flagged Retired, `hid` = cars hidden from the tower without
ever being Retired, `F1 pos valid` = share of lap completions at which F1's own
`Position` field formed a clean 1..N ranking.

| rd | Race | Laps | ret | hid | Flags | F1 pos valid | Match (`Position`) | Gap-only, before | Gap-only, after |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Australian | 1003 | 4 | 2 | VSC | 100.0% | 100.0% | 85.6% | 92.0% |
| 2 | Chinese | 919 | 6 | 1 | SC | 100.0% | 100.0% | 90.9% | 96.4% |
| 3 | Japanese | 1106 | 1 | 1 | SC | 100.0% | 100.0% | 94.1% | 94.8% |
| 4 | Miami | 1038 | 2 | 2 | SC | 87.8% | 100.0% | 92.4% | 94.1% |
| 5 | Canadian | 1206 | 3 | 3 | VSC | 99.7% | 99.3% | 73.4% | 90.5% |
| 6 | Monaco | 1445 | 4 | 2 | RED, SC | 100.0% | 100.0% | 81.0% | 95.2% |
| 7 | Barcelona | 1234 | 6 | 1 | VSC | 96.4% | 98.8% | 72.2% | 90.0% |
| 8 | Austrian | 1338 | 4 | 0 | VSC | 100.0% | 100.0% | 81.5% | 93.8% |
| 9 | British | 1111 | 1 | 1 | SC, VSC | 100.0% | 99.6% | 79.8% | 93.2% |
| 10 | Belgian | 871 | 2 | 1 | SC, VSC | 100.0% | 100.0% | 92.2% | 92.2% |
| 11 | Hungarian | 1429 | 2 | 1 | VSC | 100.0% | 99.4% | 78.2% | 92.0% |
| 12 | Dutch | 1366 | 3 | 3 | RED, VSC | 100.0% | 100.0% | 65.3% | 86.3% |
| 13 | Italian | 1052 | 2 | 1 | RED, SC, VSC | 100.0% | 100.0% | 94.1% | 94.1% |
| 14 | Spanish | 1105 | 3 | 1 | VSC | 100.0% | 100.0% | 80.3% | 87.4% |
| | **All 14** | **16,223** | | | | | **99.8%** | **82.0%** | **92.2%** |

Across every race and both modes: **0 ghost cars, 0 stale lapped gaps, 0
swallowed handler errors.** The `Position`-streaming result (98.8%-100% per
race) is unchanged by the fix below. Where F1's own `Position` was not always a
clean ranking (Miami 87.8%, Barcelona 96.4%) the ingestor briefly used the gap
ranking and still matched at 100% and 98.8%.

**The weakness.** Gap-only accuracy was 82.0% overall and ranged from 94.1%
(Monza, the race the fix was built on) down to 65.3% (Dutch). If the live socket
does not stream `Position` this is what production would have run on.

**Diagnosis** (Dutch GP, gap-only replay; every adjacent pair of published cars
compared with F1's order; 1,368,479 pairs): 12.6% were in the wrong order, and
**128,017 of the 171,790 wrong pairs (74.5%) were two lapped cars** — lapped/lapped
neighbours were wrong **34.9%** of the time, against 4.4% for two lead-lap cars
and 3.4% for a lapped car next to a lead-lap one. The cause was the tie-break
added for lapped cars in the Issue C fix: a lapped car has no seconds gap, so
they were ordered by laps down and then by their *previous position*, which
never updates — two lapped cars that pass each other stayed in the old order.
(The errors were symmetric swaps, −1 and +1, not a shift.)

**Fix (`ingest_live_session.py`, `_rank_by_gaps`).** Lapped cars are ordered the
way timing orders them: fewer laps down, then more laps completed, then whoever
crossed the line first on that lap. The feed carries no timestamps, so "first"
is the arrival order of the message in which each car's lap count last rose
(`_message_seq`, `lap_seq`), which is also deterministic in a replay. The
previous position remains only a last tie-break.

**Result:** the Dutch GP's lapped/lapped inversion rate fell 34.9% → 5.5%, its
lap-completion match 65.3% → 86.3%, and gap-only accuracy over all 14 races
82.0% → **92.2%** (range now 86.3%-96.4%).

**Still open.** Gap-only is not as good as `Position`. What remains on the Dutch
GP (4.7% of pairs in the wrong order) is mostly two lead-lap cars: of those
42,268 wrong pairs about 34% have held gaps under 1s (ordinary noise), about 26%
involve a car in or just out of the pits, and about 35% involve a gap update more
than 40s old. Those are observations; no hypothesis for the stale updates has
been tested and nothing was changed for them. The weakest races are now Dutch
(86.3%) and Spanish (87.4%).

---

## 4. Issue D — Field-wide bogus lap-4 time (real, but NOT why VER's charts were empty — see Issue E) — ✅ FIXED 2026-09-18

> **✅ Fixed 2026-09-18.** `TrackStatus` now has a real handler
> (`_handle_track_status`), accumulated per-car per-lap the same way sectors
> already were, and a new `_is_plausible_lap` (mirroring FastF1's own
> `Session._check_lap_accuracy`) now derives `is_valid`/`track_status`
> instead of the old hardcoded `True`/never-set. See "Fix summary
> (2026-09-18)" at the end of this section for the full implementation,
> real-data verification (all 21 real lap-4 AND lap-5 rows from this exact
> race now correctly reject, using only real recorded values), and one
> honestly-documented residual gap (lap 3 cannot be proven fixed
> retroactively against this already-ingested race — see that section).
> Section 0b's re-verification findings below (the lap 3/5/6 extension, the
> `TrackStatus`-already-subscribed finding) are what this fix is built on
> and remain accurate.

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

### Re-verified 2026-09-18 — confirmed, extended, and cheaper to fix than assumed

**Extension 1: the anomaly is not one lap, it's four.** The original section
found lap 4 only. Aggregating lap time by lap number across the field shows
a clear multi-lap stoppage window:

| lap | rows | avg | min | max |
|---|---|---|---|---|
| 2 | 21 | 88.5 | 86.7 | 92.3 |
| **3** | 21 | **144.7** | 119.1 | 170.0 |
| **4** | 21 | **1954.2** | 1948.7 | 1958.3 |
| **5** | 21 | **197.5** | 194.6 | 200.7 |
| **6** | 21 | **146.7** | 125.5 | 167.8 |
| 7 | 21 | 88.1 | 86.3 | 90.8 |
| 8 | 21 | 87.5 | 85.9 | 89.7 |

Laps 3, 5 and 6 are 1.4x-2.3x a normal Monza lap — individually *plausible*
(they look like safety-car / slow-down / restart laps, so no simple
magnitude threshold catches them), but they are just as unrepresentative as
lap 4 for anything that consumes lap times as real racing pace. A fix that
only excludes the ~1955s outlier still leaves three distorted laps per
driver marked `is_valid = True`.

**Extension 2: the red-flag hypothesis is confirmed from the DB alone — no
external lookup needed.** Research step 1 asked for real-world race-result
or news confirmation. The DB settles it: counting per-driver compound
changes between consecutive laps,

```
lap  4 → 20 compound changes     lap 28 → 5     lap 29 → 2
lap 13 → 1                       lap 33 → 1     lap 47 → 1
```

**20 of the 21 running drivers changed tyre compound on the same lap.** A
simultaneous whole-field tyre change is the unmistakable signature of a red
flag (a stopped race permits a free tyre change); it is not something that
can happen under green-flag racing or even under a safety car. Combined
with a ~32-minute "lap" and the slow laps either side, this is conclusive.
VER's own rows show it plainly: `SOFT` on laps 1-3, `MEDIUM` from lap 4.

**Extension 3 — the most actionable finding: `TrackStatus` is already
subscribed and then silently discarded.** The original section's option (b)
was scoped as "subscribing to `RaceControlMessages`… more correct, more
work." That's only half right. `_TOPICS` (line 70) already contains
`TrackStatus` — but the `_on_feed` dispatch has handlers for only six
topics (`_handle_car_data`, `_handle_position_data`, `_handle_timing_data`,
`_handle_timing_app_data`, `_handle_weather_data`, `_handle_driver_list`).
**There is no `_handle_track_status` anywhere in the file.** `TrackStatus`
(and `SessionInfo`) are requested from F1, received, and dropped on the
floor. That is also the direct answer to research step 4: `track_status`
is NULL on all 1052 rows not because the signal is unavailable, but because
the handler was never written. The red/yellow-flag signal needed to fix
this issue was arriving on the wire during the whole race.

**Threshold sensitivity, for designing option (a):**

| predicate | rows |
|---|---|
| `lap_time_seconds > 600` | 21 |
| `lap_time_seconds > 300` | **21** |
| `lap_time_seconds > 200` | 23 |
| `120 <= lap_time_seconds <= 200` | 65 |

A `> 300s` cut isolates exactly the 21 bogus rows and nothing else, so a
crude magnitude guard is viable as a backstop — but per Extension 1 it
provably does *not* catch the 65 distorted-but-plausible laps, which is the
argument for deriving `is_valid` from `TrackStatus` rather than from
magnitude alone.

**Other counts re-confirmed unchanged:** 1052 `lap_data` rows total,
`track_status` non-null on **0**, `is_valid` true on **1052**,
`session_elapsed_seconds` non-null on **0** (expected — CLAUDE.md documents
this is deliberately not populated on the live path), and `sector_times`
holds **0 rows across the entire database**, confirming the dead-table
observation.

### Files involved

- `backend/scripts/ingest_live_session.py` — `_handle_timing_data`,
  `_parse_lap_time`, `_TOPICS` (already lists `TrackStatus`), `_on_feed`
  (the handler dispatch with no `TrackStatus` branch)
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

### Fix summary (2026-09-18)

Implemented options (b) and (c) from research step 2 together — a real
`TrackStatus` handler plus deriving `is_valid` from a real signal, close to
the full FastF1 `_check_lap_accuracy` standard rather than a bare magnitude
cap (option (a) alone).

- **`_handle_track_status`** (new) — wired into `_on_feed`'s existing
  `TrackStatus` dispatch branch (the topic was already subscribed, see
  section 0b; only the handler was missing). Tracks one session-wide
  `_current_track_status`, defaulting to `"1"` (AllClear) so laps ingested
  before this ingestor's first-ever `TrackStatus` message aren't
  incorrectly treated as under an incident.
- **Per-car status accumulation** — `_handle_timing_data`'s existing pass 1
  (where sector accumulation and gap-state updates already happen
  unconditionally per message) now also records `_current_track_status`
  into a per-car `set[str]`, cleared into that lap's `track_status` string
  at completion, mirroring the existing `_sector_accumulator` pattern
  exactly. A car's previous completed lap's codes are kept separately for
  the check below.
- **`_is_plausible_lap`** (new, pure function) — the real replacement for
  the hardcoded `is_valid = True`. Checks, all must hold: all three sectors
  and lap time present; lap time under a 300s magnitude backstop (covers
  laps ingested before any `TrackStatus` message ever arrives); the three
  sectors sum to the lap time within 0.05s; every status code observed
  during the lap is green/yellow only (`{"1", "2"}`); and the *previous*
  lap's codes were clean too (FastF1's own check_3: "first lap after a
  safety car often has timing issues"). Deliberately does **not** attempt
  pit in/out-lap exclusion (FastF1's `PitInTime`/`PitOutTime`/
  `FastF1Generated` checks) — no live equivalent of those fields exists yet;
  left as a known, narrower residual gap, not attempted here.
- **`LapDataCreate.track_status`** (new optional field) — the one schema
  change needed so the derived value actually reaches the `lap_data` table;
  `LapData.track_status` already existed as a column, just never populated
  live.

**Real-data verification, not just synthetic fixtures.** All 21 drivers'
literal, actual `lap_data` values for laps 3, 4, and 5 of this exact race
were pulled from the local DB and fed through `_is_plausible_lap` directly
in a new permanent regression test
(`backend/tests/unit/test_ingest_live_session.py`):

- **Lap 4 (all 21 drivers) and lap 5 (all 21 drivers): confirmed rejected**,
  using only the real recorded values with the *most charitable possible*
  status assumption (`{"1"}`, no incident in the previous lap — i.e.
  assuming nothing at all is known about track status). A query run
  specifically to verify this found something the original investigation
  didn't check: **sector1 is `NULL` for every one of the 21 real rows on
  both lap 4 and lap 5** — so both laps are independently caught by the
  missing-sector check alone, with no dependency on `TrackStatus` data ever
  having existed for this race. (Lap 4's ~1955s magnitude also independently
  fails the 300s backstop — belt and suspenders, not a single point of
  failure.)
- **Lap 3: confirmed NOT closeable retroactively for this race, and the
  test says so rather than hiding it.** A query across all 21 drivers found
  every field present and self-consistent (0 of 21 rows missing a sector),
  and lap-3 times (120-170s) are well under the 300s magnitude backstop —
  the *only* signal that could have caught it is `TrackStatus`, which does
  not exist as ground truth for this already-ingested session
  (`track_status` is `NULL` for all 1052 rows — it was ingested before this
  fix). A dedicated test
  (`test_is_plausible_lap_real_monza_lap3_is_a_known_uncloseable_gap`)
  asserts the current, honest behavior (still plausible under a
  "nothing known" assumption) and documents explicitly that this is a
  structural gap the fix cannot close for Monza specifically — only for a
  genuinely live-ingested *future* race, where real `TrackStatus` messages
  would actually arrive. Lap 6 (partially sector-complete, per section 0b's
  table) has the same residual gap for its sector-complete rows.

**Checks run:** `ruff check`/`ruff format --check`, `mypy --strict` (all
three changed files), and the backend unit suite — `test_ingest_live_session
.py` alone: 78 passed (31 handler/derivation tests + 21 lap-4 + 21 lap-5 +
5 lap-3 real-data cases); full `backend/tests/unit/` suite: 377 passed, 0
failed, 0 regressions.

**Explicitly out of scope / not attempted:** pit in/out-lap exclusion (noted
above); a backfill of the existing Monza rows (`track_status` stays `NULL`
and `is_valid` stays `True` for this race's 1052 already-ingested rows — a
partial backfill using only magnitude/sector heuristics would silently miss
lap 3, which was judged worse than leaving the data as-is and documented,
matching this document's own earlier "CP5" recommendation); end-to-end
verification against a real live race (no live race occurred during this
fix — the real-data verification above is the strongest verification
available without one, but it is not a substitute for one).

---

## 5. Issue E — VER's lap/sector charts showed no data all race, while every other driver's updated correctly — ✅ FIXED 2026-09-18

> **✅ Fixed 2026-09-18.** `get_driver_laps`'s long (86400s) cache TTL is now
> reachable only when the result is non-empty, `_is_session_live` reads
> `False`, AND the session's `Race.status` is genuinely `"completed"` — a
> new `_resolve_race_status` check that doesn't depend on the same 30s Redis
> key `_is_session_live` reads, so a lapsed `gaps` key during a real
> ingestor reconnect gap can no longer poison the cache for 24h. Verified
> live against the real Monza session on the running stack (its actual
> state — `status = "scheduled"`, no `gaps` key — is exactly this bug's
> precondition): the TTL written for a real `GET /drivers/{id}/laps` call
> was ~3s, not 86400s. See "Fix summary (2026-09-18)" at the end of this
> section for the full implementation and test coverage.

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

### Re-verified 2026-09-18 — mechanism upgraded from hypothesis to documented-recurring

The code is exactly as quoted (`driver_service.py` lines 60/67 for the two
TTLs, 456-461 for the selection, 373-385 for `_is_session_live`), and the
frontend claim holds: `driverLapsQueryOptions` uses the shared key
`["driver", "laps", sessionId, driverId]`, `refetchInterval: 10_000`, and
`page_size: 100` — one page covers a full race distance, so there is
**exactly one backend cache entry per driver per session** to poison. That
is what lets a single poisoned entry blank both charts at once.

**New corroboration — this exact false negative is already documented as
having happened for real, in this codebase's own comments.** The original
section inferred the `gaps`-key-expiry risk from the reconnect handling.
Stronger evidence exists: `ingest_live_session.py`'s `_publish_live_gaps`
call site carries an inline comment explaining why it republishes on
*every* message rather than only on change —

> "confirmed live (2026 Dutch GP): gating on 'did anything change' let the
> 30s Redis TTL lapse for a minute-plus at a time whenever F1 resent
> identical gap strings for a stretch (two cars holding a stable gap to 3
> decimal places), which is common enough that **driver_service's
> live-session detection (checking whether this key exists) intermittently
> and incorrectly read as 'not live'**."

So `_is_session_live` transiently returning `False` mid-race is not a
theoretical failure mode — it is a *previously observed, previously
fixed-once* one, and the earlier fix addressed only one of its causes
(identical-gap-string suppression). A connection drop longer than 30s is a
second, independent way to produce the identical lapse, and nothing in
`get_driver_laps` is resilient to it. This moves Issue E from "leading
hypothesis" to "known failure mode with a known precedent, one unconfirmed
instance." It still is **not** directly confirmed for Monza specifically —
Redis was flushed and the containers recreated long before re-verification,
so the actual poisoned entry can never be inspected.

**Research step 3 is closed at zero cost:** `_is_session_live` has exactly
**one call site** in the entire backend — `get_driver_laps` (grep returns
only its own definition, that one call, and two test references). There are
no other consumers to audit, so the blast radius of a transient false
negative is precisely this one cache.

This also means the fix is well-contained: nothing else depends on
`_is_session_live`'s correctness, so hardening `get_driver_laps` against it
cannot regress another feature.

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

### Fix summary (2026-09-18)

Implemented research step 2's option (a) (a status-based TTL floor) plus
part of (b) (never long-cache an empty result), chosen over touching
`_is_session_live` itself — its own mechanism is reasonable, per the
research note; the gap was `get_driver_laps` trusting it alone.

- **`_resolve_race_status`** (new) — a direct `Race.status` query, scoped to
  the session, independent of the Redis `gaps` key `_is_session_live` reads.
  A live-ingested race stays `"scheduled"` for its entire life unless
  separately re-processed by `ingest_historical.py` (confirmed true for
  Monza itself — `status` is still `"scheduled"` at verification time), so
  this is a reliable signal a transient Redis gap can't fool.
- **`get_driver_laps`** rewritten so the 86400s TTL is reachable only when
  **all three** hold: the result is non-empty, `_is_session_live()` reads
  `False`, **and** `_resolve_race_status()` returns `"completed"`. An empty
  result always gets the short TTL regardless of status (research step 2's
  option (b) — the original Dutch GP dry-run finding this module's own
  `DRIVER_LAPS_TTL_SECONDS` comment already documents, an empty page is the
  highest-cost thing to freeze). The extra DB query only runs on the
  already-uncommon path (cache miss, not currently live-flagged, non-empty)
  — the common live-polling and genuinely-historical paths cost the same as
  before.

**Verified two ways**, not just unit tests:

- **Real-DB verification against the actual Monza session on the running
  stack** — its real state (`Race.status = "scheduled"`, no `gaps` key in
  Redis) is exactly this bug's precondition. A real authenticated
  `GET /drivers/{id}/laps?session_id=<monza>` call was made against the live
  backend; the Redis key written for it showed **TTL ≈ 3s**, not the 86400s
  the old logic would have written for this exact combination.
  - **Unit tests** (`backend/tests/unit/test_driver_service.py`): the
  not-live/not-completed case (short TTL — the Monza-shaped bug),
  not-live/completed (long TTL — non-regression), empty result never gets
  the long TTL regardless of status, and a genuinely-live session (`gaps`
  key present) short-circuits before `_resolve_race_status` is ever
  called (confirmed via call-count assertion, not just the resulting TTL).

**Checks run:** `ruff check`/`ruff format --check`, `mypy --strict`, and the
backend unit suite — `test_driver_service.py` alone: 11 passed (4 new); full
`backend/tests/unit/` suite: 345 passed at the time this fix landed (0
regressions against the 341 baseline CLAUDE.md's Notes recorded before this
session).

**Not directly confirmed:** whether this exact mechanism (a transient
`_is_session_live` false negative poisoning one driver's cache entry) is
what actually happened to VER's charts at Monza — Redis had already been
reset by the time of the original investigation, so that remains the
leading, well-evidenced hypothesis rather than a confirmed root cause (see
this section's own re-verification note above). What's now fixed is the
mechanism itself, regardless of whether it's specifically what hit VER that
race.

---

## 6. Cross-cutting observations (not full issues, just worth carrying forward)

- **Real DNFs this race, for reference:** LEC (1 lap), STR (26 laps), ALO
  (23 laps) all stopped short of 53 — three real retirements in one race is
  a good, realistic test case for Issue C's eventual fix and its
  verification.
- **`track_status` is entirely NULL across this whole live session** — a
  standing gap in `ingest_live_session.py`, surfaced while investigating
  Issue D but not itself chased further here. **Re-verified 2026-09-18:
  root cause found — the `TrackStatus` topic is subscribed but has no
  handler at all, so it is received and discarded. See Issue D's
  re-verification note.**
- **`SessionInfo` is likewise subscribed with no handler** (found alongside
  the `TrackStatus` finding) — same shape, unexamined consequences. Worth a
  look whenever anyone next touches `_on_feed`'s dispatch.
- **`LapCount` is now subscribed and handled** (Issue A's fix, 2026-09-19); it
  was the authoritative source of the scheduled race distance. *(Originally
  written as "not subscribed"; kept as a dated correction.)*
- **The live ingestor still records no raw feed message**, but this turned out
  not to make Issue C unfixable: F1 publishes the same per-session streams
  and they can be downloaded afterwards (see section 0c) — which is how Issue
  C was actually root-caused. A flag-gated raw recorder would still be worth
  having: the archive is what F1 recorded server-side, and only a recording
  made by the ingestor itself can show what the live socket really delivered
  (e.g. whether `Position` streams live).
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

Updated 2026-09-18 to reflect the re-verification pass (section 0b).

| Issue | Correct behavior | Current behavior | Confirmed root cause | Fix complexity (revised 2026-09-18) |
|---|---|---|---|---|
| A: pit window > race length | Never recommend past the last lap | 74% of all predictions exceeded race length (max lap 93); at lap 38 **every** driver showed lap 78 | Two layers: unbounded `optimal_pit_lap` + `total_laps` proxy that collapses to ≈`current_lap` mid-race, making the capped path return NULL 94.6% of the time | **✅ Fixed 2026-09-19 (4 checkpoints)** — real `Session.total_laps` (schema + both ingestion paths), 3 consumers switched to prefer it (a 3rd found beyond the original 2), `optimal_pit_lap` clamped, frontend surfaces uncertainty honestly. 155/158 real sessions backfilled; 429 backend + 56 web tests passing |
| B: undercut score is bistable and race-state-blind | Alert reflects a realistic, stable strategic threat | Score swings 0.07 → **1.00** → 0.01 on consecutive laps; 33 alerts fired, mostly for a back-marker or a retired rival | **Re-established 2026-09-19:** the starting gap was wrong for a live session (summed lap times omit lap 1 for every driver; the rival came from stored positions that still contained retirees), plus an alert-layer defect (pairs built from each driver's latest stored row, so a retiree stayed in the order) | **✅ Fixed 2026-09-19 (CP3-CP5)** — gap and rival from F1's live standings (live-and-same-session payloads only, old path kept otherwise); alert pairing from the live standings; alerts suppressed for tyres <= 3 laps old and < 15 laps left (stored score unchanged). Monza offline: 'already ahead' 29.9% → 0.5%, gap error 2.15s → 0.28s, scores >= 0.999 185 → 74, alerts 46 → 29 (16 → 1-2 involving a stopped car). Not yet seen on a real live race |
| C: retired driver frozen in timing tower | Retiree drops out of active standings | LEC shown all race after a lap-1 retirement; ALO/STR and four lapped cars frozen too | F1 never sends a `"RETIRED"` string — it sends `Retired`/`ShowPosition`/`Stopped` booleans; lapped cars send `"1 L"`/`"52L"`, which the parser never matched | **✅ Fixed 2026-09-19 (CP1-CP2)** — root-caused from F1's archived feed (not impossible, as first written). Retired/hidden/stopped cars leave the ranking; lapped strings parsed; ranking from F1's `Position` once streaming, gap-based fallback. Full-race replay: position match 11.1% → 100% (94.1% on the fallback), ghost cars 3 → 0, stale lapped gaps 0. Across 14 archived 2026 races: 99.8% with `Position`, 92.2% gap-only (82.0% before the lapped-car ordering fix), 0 ghost cars. Live-socket delivery of `Position` unconfirmed |
| D: bogus lap times around a red flag | Implausible/unrepresentative laps flagged, not stored as valid racing laps | Laps **3-6** distorted (lap 4 ≈1955s), all marked `is_valid=True`; `track_status` NULL everywhere | No plausibility check; `is_valid` hardcoded `True`; **and `TrackStatus` is subscribed but has no handler**, so the flag signal is received and discarded | **✅ Fixed 2026-09-18** — `_handle_track_status` + `_is_plausible_lap` (FastF1-equivalent checks). All 21 real lap-4 AND lap-5 rows confirmed rejected using real DB values; lap 3 confirmed NOT closeable retroactively for this race (no real `TrackStatus` ground truth exists), documented rather than hidden |
| E: one driver's charts showed no data all race | Every driver's live chart updates identically | Only VER's lap/sector charts never updated; every other driver's did | `get_driver_laps` picks its TTL from `_is_session_live`, which is **documented to have transiently read `False` mid-race before** (2026 Dutch GP); an unlucky cache population then gets an 86400s TTL | **✅ Fixed 2026-09-18** — `get_driver_laps` now also floors on real `Race.status == "completed"`, independent of the Redis key `_is_session_live` reads. Verified live against the real Monza session (its exact precondition): TTL written dropped from would-be 86400s to ~3s |

None of these affect historically-ingested (post-race, `ingest_historical.py`)
sessions — all five are specific to the live path
(`ingest_live_session.py` and the live-serving branches of
`strategy_service.py`/`prediction_worker.py`/`driver_service.py`/the
frontend hooks that key off live telemetry).

---

## 7b. Progress log — the 2026-09-19 session

### Starting point and scope
Issues A, D and E were already fixed (2026-09-18/19). This session's brief was
to re-verify the document's findings against the code and the real Monza data,
then fix **B** and **C**, then build the verification needed to trust the
fixes. Nothing was committed or deployed by the session; committing, the PR and
the CLAUDE.md update are left to the project owner.

### What was done, in order

| Step | What | Outcome |
|---|---|---|
| Re-verification | Re-read B and C against the code and the local Monza data; downloaded F1's archived per-session feed for the race (`fastf1._api.fetch_page`) | B's cause was the starting gap, not bistability; C's "evidence impossible" was wrong — the archive showed F1 never sends `"RETIRED"` (section 0c) |
| **CP1** | Replay harness `verify_live_feed_archive.py`: drives the unmodified ingestor with F1's recorded messages, audits the published standings against F1's own per-car state; 31 KB real-message fixture | Baseline: position match 11.1%, ghost cars LEC/ALO/STR, stale gaps on 4 lapped cars |
| **CP2** | Ingestor: retirement by `Retired`/`ShowPosition`/`Stopped`, lapped-string parsing (`"1 L"`, `"52L"`), ranking from F1's `Position` once seen streaming with a gap-based fallback | Monza: position match 100% (94.1% on the fallback), ghost cars and stale lapped gaps 0 |
| **CP3** | `strategy_service._live_gap_deficit`; undercut/overcut gap and neighbours from F1's live standings (live + same-session payloads only, old path kept otherwise) | Spot check: VER lap 30 deficit +0.22s vs −0.99s from summed lap times |
| **CP4** | Offline re-score of all 1052 Monza predictions, old gaps vs live gaps (`evaluate_undercut_live_gaps.py`, read-only) | "Already ahead" 29.9% → 0.5%; gap error 2.15s → 0.28s; scores ≥ 0.999 185 → 74 |
| **CP5** | `alert_service`: pairing from live standings; alerts suppressed for tyres ≤ 3 laps old and < 15 laps left | Alert replay 46 → 29; alerts involving a stopped car 16 → 1 |
| **CP6** | This document | Sections 0c, B/C fix summaries, table rows |
| Data | Monza `sessions.total_laps = 53` (local Postgres; guarded `WHERE total_laps IS NULL`) | Value from F1's own `TotalLaps`; `Race.status` deliberately left `scheduled` |
| Ops | `docker compose restart worker` (bind-mounted `backend/`, so a restart loads the new code); verified `ready`, changed symbols present in the container, imports clean | — |
| **V4** | 13 property-test cases (scoring, live deficit, ranking, alert gates), plus a mutation check | 8 of 8 deliberate breakages caught by the property tests (and by the example tests); files restored, checksums verified |
| **V1** | Replayed all 14 completed 2026 races, both ranking modes (`--rounds 1-14`) | 16,223 lap completions; ghosts/stale lapped gaps/handler errors 0; `Position` 99.8%, gap-only 82.0% |
| Fallback fix | Diagnosed the gap-only weakness on the Dutch GP (74.5% of wrong pairs were lapped/lapped); lapped cars now ordered by laps completed then line-crossing order | Gap-only 82.0% → 92.2%; `Position` path unchanged |
| **V5** | Always-on counters and logs; flag-gated raw-feed recorder (`recordings/`; on by default in the Docker stack); harness `--recording` mode; worker recreated for the new env and mount | Built and verified on replayed data; awaiting a live race (section 7c) |
| **V3** | Shadow-race harness `shadow_race.py` (`run` / `verify` / `cleanup`): feeds Monza's archived messages through the real ingestor into the running Docker stack under a throwaway season-2098 race, then runs 14 automatic checks | Smoke (start to lap 12 of 53, 1x): 14 of 14 pass after two production fixes. It first found a `dispose()` bug that could wedge the pipeline and a lap-persist ordering race (6.4% of laps without a prediction), both fixed (section 7d). Full-race run not done yet |

### Decisions and the reasons
- **B: gap and target from F1's live standings (Option 1), not a patched lap-time sum.** F1's gap is authoritative; a corrected sum would still be an approximation from ingest-time jitter. Live payloads are trusted only with `source == "live"` and a matching `session_id`; otherwise the old path runs, so replays and historical sessions are unchanged.
- **C: `Stopped` hides a car** until cleared. It was only ever true for the three retirees in Monza (107 `InPit` events, none with `Stopped`), and `Retired` can arrive ~25 minutes after a car stops.
- **C: `Position` first, gap-based fallback kept.** The archive shows `Position` complete, but the 2026 Dutch GP live run saw it only in the snapshot, so `Position` is trusted only after it has been seen on a live diff.
- **CP5: the gates suppress the alert, not the score.** The stored `undercut_score` stays an honest probability; thresholds (tyre age ≥ 4, laps left ≥ 15) came from the measured Monza buckets and give 26-35 alerts at neighbouring values (29 chosen), so they are not on a knife edge. The remaining-laps gate needs the real `Session.total_laps` and is off when unknown.
- **Monza `total_laps`: filled, status not flipped.** Marking Monza `completed` would change which race the Strategy Simulator and Driver Style page pick, and Driver Style would likely break for 2026 (no tyre-degradation backfill for Monza).

### Corrections made along the way
- The lapped-car tie-break added in CP2 (previous position) was the largest source of gap-only error; V1 exposed it and the fallback fix replaced it.
- CP4 found a defect that was not in the original brief: `alert_service` built its pairs from each driver's latest stored lap row, so a retiree stayed in the order (CP5).
- A test helper (`_scalar_result`) only stubs `scalar_one`, so a query reading `scalar_one_or_none` silently got `float(MagicMock()) == 1.0`; one existing undercut test passes on that coincidence. Left as is; new tests use an explicit `_elapsed_result`.
- The unit-suite baseline at the start of the session was 429, not the older 341 in CLAUDE.md.

### Files
- **Production code changed:** `backend/scripts/ingest_live_session.py` (CP2, lapped ordering), `backend/services/strategy_service.py` and `backend/workers/prediction_worker.py` (CP3), `backend/services/alert_service.py` (CP5).
- **Dev tools:** new `backend/scripts/verify_live_feed_archive.py` (CP1; `--rounds`, `--no-position-diffs`, `--excerpt`, `--write-excerpt`, `on_publish` hook) and `backend/scripts/evaluate_undercut_live_gaps.py` (CP4/CP5); `backend/scripts/verify_live_feed_parity.py` now sends the real Retired/Stopped/ShowPosition booleans instead of the invented string.
- **Tests:** new `test_verify_live_feed_archive.py`, `test_evaluate_undercut_live_gaps.py`, fixture `backend/tests/unit/fixtures/monza_2026_r13_timing_excerpt.json` (31 KB of real F1 messages); extended `test_ingest_live_session.py`, `test_strategy_service.py`, `test_prediction_worker.py`, `test_alert_service.py`.
- **V5:** new `backend/scripts/_raw_feed_recorder.py` and `backend/tests/unit/test_raw_feed_recorder.py`; counters in `ingest_live_session.py`, `strategy_service.py`, `prediction_worker.py`, `alert_service.py`; `--recording` in `verify_live_feed_archive.py`; settings in `backend/core/config.py`; `.env.example`, `.gitignore` (`/recordings/`) and `infra/docker/docker-compose.yml` (worker env + `./recordings` mount).
- **V3 (added later the same day):** new `backend/scripts/shadow_race.py` and `backend/tests/unit/test_shadow_race.py` (36 tests); **two production fixes in `backend/workers/prediction_worker.py`** (the engine `dispose()` in `_persist_and_publish`, and the bounded retry in `run_strategy_prediction`) with five new tests in `test_prediction_worker.py`. See section 7d. These two are outside the original B/C scope; they were made because the shadow race exposed them.
- **Data / environment:** local Postgres `sessions.total_laps` for Monza; the worker container restarted several times (plain `docker restart docker-worker-1` is enough for code changes — `backend/` is bind-mounted; recreate with `--env-file .env` only when compose settings change). Supabase, S3 (read-only use), and the real Monza rows and Redis keys were not modified (the shadow harness checks this itself). A throwaway season-2098 race remains locally (section 7d).

### Checks, end of session
Unit suite 429 → 458 (CP1) → 481 (CP2) → 501 (CP3) → 513 (CP4) → 539 (CP5) → 564
(V4, V1) → 568 (fallback fix) → **607** (V5), 0 failures throughout. Integration tests (real
Postgres and Redis): 4 (CP2), 16 (CP3), 23 (CP5), 4 (fallback fix, ingestor
tests only), 23 (V5). `mypy backend/ --strict` and `ruff check` / `ruff format --check`
clean repo-wide at every checkpoint (154 files at the end). The older DB-driven
parity harness stayed at 99.0% position / 94.1% tyre age (Belgian GP).

### Reproducing the measurements
```
python -m backend.scripts.verify_live_feed_archive --season 2026 --round 13            # one race
python -m backend.scripts.verify_live_feed_archive --season 2026 --rounds 1-14         # V1 table (~7 min)
python -m backend.scripts.verify_live_feed_archive --season 2026 --round 12 --no-position-diffs   # gap-only fallback
python -m backend.scripts.verify_live_feed_archive --excerpt backend/tests/unit/fixtures/monza_2026_r13_timing_excerpt.json
python -m backend.scripts.evaluate_undercut_live_gaps                                  # CP4/CP5 (~90 s; needs DB + S3)
```

---

## 7c. Remaining verification and open work

The fixes are proven on the inputs (gaps, targets, rankings) and on one real race
plus 14 archived ones. Four things are not yet established: that the score means
what it says (V2), that the pieces work together across real processes (V3),
what the live socket actually delivers (V5), and how good the gap-only fallback
can get (lead-lap work). Planned order: **V5 (now built), then V3 and V2 while the next
race is awaited, then the lead-lap fallback only if V5 shows the fallback is
what runs live.** V6 (case studies) folds into V2. V4 and V1 are done (7b).
**Status at the end of the 2026-09-19 session:** V3 is built and smoke-tested (full
results and the two bugs it found are in section 7d); its full-race run is still to do.
V2 and the lead-lap work are untouched.

### V5 — next-race instrumentation and a raw-feed recorder (built 2026-09-19; awaiting a live race)
**Goal.** Settle whether the live socket streams `Position`, and make a real live
race replayable exactly as delivered.

**What was built.**
- **Always-on counters and logs.**
  - The ingestor logs once when F1's race-order `Position` field first streams on a
    live diff (with the TimingData message number), and at the end of a session logs a
    summary — with a **warning if `Position` never streamed**, meaning the whole race
    ran on the gap-based fallback.
  - The ingestor keeps counters (`timing_messages`, `laps_dispatched`,
    `rankings_by_f1_position` / `rankings_by_gaps`, `cars_flagged_out`,
    `connections_opened`, `subscribe_snapshots`, `position_first_message_seq`, the
    recording path) and writes them as JSON to **`f1:{season}:{round}:ingest_stats`**
    (at most every 15s, kept 24h).
  - The strategy pipeline keeps a Redis hash **`f1:{season}:{round}:pipeline_stats`**
    (24h): `gap_source_live` / `gap_source_summed` (which gap the undercut maths used),
    `neighbors_source_live` / `neighbors_source_db`, `alert_order_source_live` /
    `alert_order_source_db`, `alerts_suppressed_tyre_age`,
    `alerts_suppressed_laps_remaining`, `alerts_dispatched`. Counting is best-effort: a
    Redis error is logged and ignored, never raised. Counts include non-live sessions
    (replays, historical), so read them in the context of the session.
- **Raw-feed recorder** (`backend/scripts/_raw_feed_recorder.py`), **on by default in the
  Docker stack** (the code's own default is off, so a manual run on the host does not record
  unless asked).
  With `RECORD_RAW_FEED=true` the ingestor also writes every message it receives from
  F1's socket to one gzip'd JSONL file per session,
  `recordings/<season>_R<round>_<type>_<UTC start>.jsonl.gz`: one line per message
  (`t` receive time, `topic`, `data`), the Subscribe snapshot (the only place the
  initial state arrives), and `opened`/`closed` connection events. Recorded topics:
  TimingData, TimingAppData, TrackStatus, DriverList, LapCount, WeatherData,
  SessionInfo. **`CarData.z` / `Position.z` are never recorded** — they need F1TV and
  carry nothing in no_auth mode (CLAUDE.md), and they are unrelated to the ranking,
  gap and alert logic. (Not to be confused with the `Position` *field inside
  TimingData*, the race order, which needs no F1TV and is what this is about.)
  A write failure (`OSError`) or an unserialisable message is logged and never raised
  into the feed callback; a disk error stops recording for the session, not ingestion.
- **Harness:** `--recording PATH` (with `--season/--round`) prints a summary, the answer
  to "did `Position` stream on the live socket?", a per-topic table of recording vs F1's
  archive of the same session, and then replays the recording through the ingestor.
- **Wiring:** `RECORD_RAW_FEED` (code default false; `docker-compose.yml` defaults it to true) and `RAW_FEED_RECORD_DIR` in
  `core/config.py`'s `LiveTimingSettings`; `.env.example`; `docker-compose.yml`'s worker
  gets both variables and mounts the host's `./recordings` at `/recordings`;
  `/recordings/` is in `.gitignore`.

**Runbook — no manual step.** Starting the containers is enough: `docker-compose.yml`
defaults `RECORD_RAW_FEED` to `true` for the worker, and the auto-launched ingestor
inherits it. Leave the stack running — `beat` checks every 5 minutes, the worker launches
the ingestor about 30 minutes before the race start (auto race detection is on by
default), and the ingestor opens its file in `./recordings/`. One file of a few MB per
auto-detected race (Race sessions only; practice and qualifying are not auto-launched).
Recordings are not cleaned up automatically.
- **Turn it off:** set `RECORD_RAW_FEED=false` in `.env` and recreate the worker so it
  re-reads the environment (a plain `restart` does not):
  `docker compose -f infra/docker/docker-compose.yml --env-file .env up -d --force-recreate worker`.
- **Only applies to the Docker worker.** A manual run on the host (`make ingest-live`) uses
  the code default (off) unless `RECORD_RAW_FEED=true` is in the host environment / `.env`,
  and then writes to `recordings/` in the working directory. The counters are always on.

**After the race.** Read the counters the same day (24h TTL):
`docker exec docker-redis-1 redis-cli GET f1:<season>:<round>:ingest_stats` and
`... HGETALL f1:<season>:<round>:pipeline_stats`; the ingestor's log lines are in the
worker's output (`docker logs docker-worker-1 2>&1 | grep -E "Position field|Live ingest summary|never streamed"`).
With a recording:
`python -m backend.scripts.verify_live_feed_archive --recording recordings/<file> --season <s> --round <n>`.

**Verified.** 39 new tests (recorder, ingestor hooks, counters, the harness's recording
mode, and a round trip: the committed real-message excerpt is fed through a recording
ingestor, written, read back, replayed, and compared with its source archive
message-for-message); a mutation check (6 deliberate breakages — recording the `.z`
topics, a disk error raising into the callback, the once-only log, the replay offset,
the counter names, the dropped snapshot — all caught, files restored and
hash-verified); the recorder wrote through the worker container's mount to the host;
the flag reads `False` by default and `True` when set; replay accuracy unchanged
(Monza 100.0% / 94.1% gap-only, DB-driven harness 99.0% / 94.1%). Unit suite 607
passed, integration 23 passed, `mypy --strict` and `ruff` clean.

**Done when.** After a live race we can state: whether and when `Position` first
streamed (or that it never did), the share of rankings by source, the socket-vs-archive
message-count difference per topic, and that the recording replays through the harness.

**Limits and open points.**
- **Nothing here has met a real socket yet.** The recorder and counters are proven on
  replayed data and in tests; whether they behave on a live connection is confirmed by
  the first race with the flag on.
- Only the first Subscribe snapshot is replayed as a snapshot; later ones (reconnects)
  are counted in the summary but not replayed as snapshots.
- The compressed size per race has not been measured (raw TimingData was ~6 MB for
  Monza); expect a few MB at most.
- Local development only: a Fly.io production container's disk is temporary, so a
  persistent volume or an S3 upload would be needed there (live ingestion is not wired
  into a Fly.io process yet).
- **CLAUDE.md** (updated 2026-09-19): the two new Redis keys (`f1:{season}:{round}:ingest_stats`,
  string JSON, 24h; `f1:{season}:{round}:pipeline_stats`, hash, 24h), the two environment
  variables (`RECORD_RAW_FEED`, `RAW_FEED_RECORD_DIR`) and a note under Auto Race Detection.
**Effort.** Done. **Cannot show** anything before a race with the flag on.

### V3 — shadow race on the local stack (built and smoke-tested 2026-09-19; full race pending — results in section 7d)
**Goal.** Verify the cross-process wiring that unit tests mock: ingestor → real
Redis → Celery worker → `_resolve_position_context` / `_live_gap_deficit` →
`strategy_predictions` → `evaluate_threats` → `alerts`.
**Method.** Feed Monza's archived messages through the real ingestor into the
running docker stack under a **throwaway session and race** (never the real Monza
rows), sped up, with a test subscriber. Assert automatically: `total_laps` set from
the lap count; no retired car in the published `gaps` after its flag; predictions
used live deficits (needs V5's counter or a log check); no alert involves a
stopped car, a driver on tyres ≤ 3 laps old, or with < 15 laps left; persisted
`lap_data.position` matches F1's archived positions (target ≥ 99% in `Position`
mode); no worker errors. Delete the throwaway rows afterwards.
**Risks.** Speed-up changes worker lag relative to real time (report it, don't
hide it); DB pollution (isolate and clean up); alert delivery to real users
(use a test user only). **Cannot show** live-socket behaviour.
**Effort.** Medium. Easier after V5.

### V2 — historical outcome calibration (with V6 folded in)
**Goal.** The only check of whether `probability_pit_now_gains_position` means
what it says — nothing so far has tested the score against what happened.
**Method.** From 2018-2025 (163,623 laps; historical gaps are correct via
`session_elapsed_seconds`), find real situations where driver D is directly
behind T within a few seconds, D pits at lap L while T stays out and pits later;
label the outcome as whether D is ahead of T after both have stopped. Compute the
score as of lap L with the promoted models and compare with the outcome:
reliability curve, Brier score, AUC, against a gap-only baseline. Evaluate on a
season the models were not trained on (they were trained on 2018-2024 with 2025
held out). Also report outcome rates for the gate conditions.
**Step 0.** Count the eligible events first; if the held-out season has too few
for meaningful intervals, widen it and say so.
**V6 inside it.** Review the Monza alerts that remain (`VER on RUS` ×12, `VER on
ANT` ×7) against the race's real pit stops in `tire_stints`.
**Caveats.** Selection bias (teams pit when they expect it to work, so only
attempted undercuts are observed); the score assumes a specific "pit now vs next
lap" framing that real stops only approximate. **Decision rule.** If calibration
is poor or no better than the gap-only baseline, the score and the 0.5 alert
threshold need redesign — that is a finding, not a failure of the fixes above,
which concern the inputs.
**Effort.** Medium-high.

### Lead-lap gap-only fallback (conditional on V5)
**Where it stands.** Gap-only position match is 92.2% over 14 races (range
86.3%-96.4%; weakest Dutch 86.3%, Spanish 87.4%) against 99.8% with `Position`.
On the Dutch GP 4.7% of adjacent pairs are still in the wrong order, mostly two
lead-lap cars (42,268 wrong pairs, 4.4% of that pair type): ~34% have held gaps
under 1s, ~26% involve a car in or just out of the pits, ~35% involve a gap
update more than 40s old. No hypothesis has been tested.
**Hypotheses, one at a time, each measured on the 14 races.**
1. *Stale gaps (> 40s old):* find what produces them (message patterns, pit lane,
   red-flag/safety-car periods) and whether to discount or extrapolate them.
2. *Pit lane / pit exit:* a car's gap through the pits may not track its position
   order; test special handling around `InPit`/`PitOut`.
3. *Small-gap noise (< 1s):* hysteresis on adjacent swaps, weighing the lag it
   adds to genuine overtakes.
**Method.** Promote the scratch diagnosis (classifying adjacent inversions by pair
type, gap size, staleness, pit involvement) into the harness (`--diagnose`); keep
a change only if gap-only accuracy improves without hurting the `Position` path or
the lapped-car result; add unit and property tests for what stays.
**Proposed targets (to be agreed):** gap-only ≥ 95% overall and ≥ 90% on the worst race.
**Trigger.** Only if V5 shows the live socket does not stream `Position`; if it
does, this path is a rarely-used safety net and the effort is better spent elsewhere.
**Effort.** Medium.

### Other open limits (from sections 0c, B and C)
- Live-socket delivery of `Position` — V5 (built; answered by the first race run with the recorder on).
- `alert_worker._dispatch` (`backend/workers/alert_worker.py`, around line 112) has the same flaw as the one fixed in `prediction_worker._persist_and_publish`: its `get_engine().dispose()` sits after the `try`, so an exception skips it. Deliberately **not** changed (outside the brief; it is the FCM push path, which is not configured here). Small fix, same pattern, needs the owner's OK (section 7d).
- The prediction retry (section 7d) is bounded: 4 retries, 3 s apart. A lap that never reaches `lap_data` still ends as a failed task, and a retried prediction is delayed by a few seconds (p95 lag 14 s → 31 s in the smoke run). It is a mitigation for an ordering the design does not guarantee; chaining the two tasks would remove the race but touches the telemetry worker, the ingestor, and the replay/parity tools.
- The 0.5 alert threshold, the gate constants and the 60s dedup are not derived
  from outcomes — V2.
- Existing Monza rows (`lap_data.position`, `strategy_predictions`, alerts) keep
  their pre-fix values.
- Replays and historical sessions still form the alert pairing from stored rows,
  where retirees linger.
- The alert target is not stored with the prediction; the alert pairs the trailing
  car with the live-adjacent one, which is normally the worker's target too.
- The remaining-laps gate needs `Session.total_laps`; live sessions get it from the
  lap count, but any session without it runs with that gate off.
- Model artifacts load on the dev host with scikit-learn/XGBoost version-mismatch
  warnings (saved on 1.9.1, loaded on 1.9.0); evaluation numbers were produced under it.

### Unrelated leftovers noticed
`SessionInfo` is subscribed with no handler; the `SectorTime` table is unused
(section 6); `isReplayActive` is misnamed (section 6); the `resilience` test
marker is not selected by any CI workflow (Issue A's fix summary).

---

## 7d. V3 — the shadow race: what was built, what it found, what is left (2026-09-19)

### What it is
`backend/scripts/shadow_race.py` (tests: `backend/tests/unit/test_shadow_race.py`, 36 tests).
It replays Monza's archived F1 messages through the **real** `F1SignalRIngestor` into the
**running Docker stack** (real Redis, the real Celery worker, real Postgres) under a
throwaway race — **season 2098, event name starting "SHADOW RACE"** — so nothing real is
touched, then checks the outcome automatically. It proves the cross-process wiring that
unit tests mock; it cannot say anything about F1's live socket.

```
python -m backend.scripts.shadow_race run --until-lap 12 --speed 1 --manifest recordings/shadow/smoke.json   # smoke (~25 min)
python -m backend.scripts.shadow_race run --speed 2 --manifest recordings/shadow/full.json                   # full race (roughly an hour, estimated)
python -m backend.scripts.shadow_race verify  --manifest <path>     # waits for the queues to drain, then runs the checks
python -m backend.scripts.shadow_race cleanup --manifest <path>     # omit --manifest to remove every shadow race
```
Other `run` options: `--prerace-speed` (default 60: the pre-race hour is skipped quickly until the
first lap completes — without it the first attempt spent an hour before lap 1), `--max-gap`
(cap on dead air, 5 s), `--source-season/--source-round/--real-session-id` (default Monza). `run`
refuses to start while a real live race is being ingested — **never run it during a real race**.
Cleanup only deletes season-2098 "SHADOW RACE" races (cascading to sessions, laps, predictions,
alerts) and that race's Redis keys.

### The 14 checks (all automatic)
Queues drained; `sessions.total_laps` set from F1's lap count (53); no out-of-race car or stale
lapped gap in the published standings and no swallowed handler errors; every lap completion
reached `lap_data` through Celery; persisted `lap_data.position` matches F1's (>= 99%); a
prediction exists for every dispatched lap (>= 99%); prediction lag (reported, not judged);
gaps, neighbours and alert pairing all came from live standings (counters); no alert involves a
stopped car, tyres <= 3 laps old or < 15 laps left; ingest stats show F1's `Position` streamed and
drove the ranking; the raw-feed recording matches what was fed, topic for topic; the real Monza
rows and Redis keys are unchanged; no `ERROR`/`CRITICAL` line in the worker log during the run
(WARNING-level noise such as an Ergast traceback is ignored on purpose); peak queue depth
(reported). Queue depth counts kombu's `unacked` hash too, because a task the worker has prefetched
leaves the Redis list and would otherwise make a busy worker look idle.

### What it found — two real production bugs, both fixed
1. **`prediction_worker._persist_and_publish` skipped `get_engine().dispose()` when anything
   raised.** The dispose came after the `try` block, so one failed prediction left a pooled
   asyncpg connection bound to a closed event loop, which the next task's `asyncio.run` then
   collided with. In a real race one bad prediction could therefore take down the predictions
   that followed it. Same shape as the bugs already fixed in `_run_simulation` and `telemetry_worker`
   (CLAUDE.md, Notes). Fixed by disposing in a nested `finally` (Redis client closed first; a failing
   Redis close still disposes). Two new tests fail against the old shape and pass against the fix.
2. **A prediction could run before its lap was written.** The ingestor sends `process_lap` to
   `telemetry_queue` and `run_strategy_prediction` to `prediction_queue` back to back
   (`ingest_live_session.py`, lines ~830-831); one `--pool=solo` worker consumes both queues and nothing
   orders them. At a lap boundary, when every driver completes together (queue peak 21 tasks), some
   predictions ran first and raised `NotFoundError: No lap data for driver in session`: **15 of 233
   laps (6.4%) had no prediction, so no undercut score and no alert for them.** This existed before
   this session; bug 1 was hiding it (it wedged the pipeline first). Fixed with a bounded retry in
   `run_strategy_prediction` (bound task, up to 4 retries, 3 s apart, `NotFoundError` only; once the
   retries are spent the error still fails the task). Three new tests: retries on `NotFoundError`,
   does not retry other errors, does not retry on success.

Tool bugs fixed along the way (not production): multiple `asyncio.run` calls on the shared SQLAlchemy
engine gave "Event loop is closed" (the tool now uses a fresh pool per phase); the log check counted a
WARNING-level traceback as an error; queue depth ignored prefetched tasks.

### Results of the smoke run (start to lap 12 of 53, 1x, 233 lap completions)
| | Run 1 (before the retry) | Run 2 (with the retry) |
|---|---|---|
| Predictions per dispatched lap | 218 of 233 (93.6%) — FAIL | **233 of 233 (100%)** |
| `NotFoundError` lines in the worker log | 15 — FAIL | **0** |
| Laps persisted / position correct | 233 / 233 (100%) | 233 / 233 (100%) |
| Prediction lag median / p95 / max | 3.7 / 13.7 / 18.7 s | 4.0 / 30.9 / 37.5 s |
| Gaps live/summed; neighbours live/db; alert pairing live/db | 423/0; 233/0; 218/0 | 445/0; 247/0; 233/0 |
| Alerts (stopped car / fresh tyre / late race) | 12 (0/0/0) | 12 (0/0/0) |
| Ghost cars, stale lapped gaps, swallowed handler errors | 0 | 0 |
| F1 `Position` drove the ranking | yes (first seen at message 2206; 3 cars flagged out) | same |
| Recording matches what was fed | yes | yes |
| Real Monza data untouched | yes | yes |
| Queue peak | 21 | 21 |
**Run 2: 14 of 14 checks pass.** The rise in p95 lag is the retried laps waiting a few seconds; it is well
under a lap. Run 1's numbers are kept in `recordings/shadow/smoke_run1.{json,log}` (local, gitignored).

### What this does and does not show
- **Shows:** the live-standings path (gaps, neighbours, alert pairing) is used end to end across real
  processes; persisted positions match F1's; the pipeline survives load at a lap boundary once the two
  fixes are in; the recorder and counters work through the worker; and the harness catches this class of
  bug (it found two in its first proper run).
- **Does not show:** anything from F1's real socket (V5's job, on the next race); behaviour after lap 12
  — most pit stops, later retirements (the smoke run flagged 3 cars out), safety cars, the
  finish and `total_laps`-driven gates late in the race are only covered by the full run; whether the
  score means what it says (V2). It ran at 1x, so the speed-up's effect on worker lag is untested.
  It ran on a warm worker; a cold start (~88 s of imports) was not exercised.

### State left behind (clean this up)
- **A throwaway race is still in the local Postgres and Redis:** season 2098, round 1 ("SHADOW RACE ..."),
  from smoke run 2; manifest `recordings/shadow/smoke.json`, log `recordings/shadow/smoke.log`, recording
  `recordings/2098_R01_R_20260919T142436Z.jsonl.gz`, and the run-1 copies `smoke_run1.*`. All of `recordings/`
  is gitignored. Run `python -m backend.scripts.shadow_race cleanup` (no manifest = every shadow race) **before
  starting the full run** so the round number and Redis keys start clean, then delete the leftover files under
  `recordings/`.
- The worker container was restarted (`docker restart docker-worker-1`) and is running the fixed code;
  confirmed by grepping the constant `_LAP_NOT_YET_PERSISTED_RETRIES` inside the container. Use
  `MSYS_NO_PATHCONV=1` for `docker exec` paths from Git Bash.
- Real Monza data was verified intact by the harness (row counts unchanged, no new real Redis keys).

### Checks at the end of the session
`pytest backend/tests/unit -m unit`: **648 passed**. Integration (`test_live_prediction_pipeline`,
`test_race_simulation_serialization`, `test_strategy_endpoint`): **14 passed** (the wider set of 23 was last run
before the V3 changes; re-run the whole integration folder before committing). `mypy backend/ --strict` and
`ruff check` / `ruff format --check`: clean (158 files).

### To do next for V3, in order
1. `shadow_race cleanup`, then the **full race**: `run --speed 2 --manifest recordings/shadow/full.json`, then
   `verify`. Expect roughly an hour (12 laps took 25 minutes at 1x). Watch for: `NotFoundError` and retry exhaustion,
   every retired car (LEC lap 1, ALO/STR and others) leaving the standings and the alerts, the late-race laps gate, lag at 2x, the
   queue peak at each pit-stop cluster, and total worker errors.
2. Add the full-run results to this section, replacing the "full run pending" wording here, in section 7b and in the
   top status note.
3. Run `shadow_race cleanup` again and confirm season 2098 is gone (and the real Monza counts still match).
4. Decide with the project owner: fix `alert_worker._dispatch`'s dispose (section 7c, open limits), and whether to chain
   `process_lap` → prediction instead of retrying.
5. ~~CLAUDE.md note~~ — done: a Notes entry for the two `prediction_worker` fixes and `shadow_race.py` was added
   (2026-09-19). Update it with the full-run result once step 1 is done.

---

## 8. Anchor Prompt for Resumption — paste into the new session

### Current anchor (2026-09-19, end of the B/C/V-series session)

```
Read docs/live-race-ingestion-and-strategy-gaps-monza-2026.md before anything
else: first the status note at the very top, then section 0c, 7b, 7c and 7d
(7d is the most recent work). Then read CLAUDE.md as usual.

Context. The Italian GP 2026 (Monza, Round 13) was the first full live-ingested
race. Five issues (A-E) were found. All five are fixed: A, D, E earlier; B (undercut
score/alert inflated by a wrong starting gap and ghost rivals) and C (retired cars
frozen in the timing tower) on 2026-09-19, by taking gaps, rivals and the ranking
from F1's live standings. Verification tooling built: a replay harness over F1's
archived feed (verify_live_feed_archive.py; V1 replayed 14 races), property tests
(V4), next-race counters + a raw-feed recorder that is on by default in Docker (V5),
and a shadow-race harness (backend/scripts/shadow_race.py, V3). V3's smoke run
(start to lap 12 of 53) found and got fixed two real bugs in
backend/workers/prediction_worker.py: a skipped engine dispose() after a failed
prediction, and a race where a prediction ran before its lap was persisted (fixed
with a bounded retry). The smoke run now passes 14 of 14 checks.

What is NOT done:
 1. V3's full-race run (a throwaway season-2098 race from the smoke run is still in
    the local DB/Redis — run `python -m backend.scripts.shadow_race cleanup` first,
    then `run --speed 2 --manifest recordings/shadow/full.json`, then `verify`),
    documenting its results in 7d/7b/the top note, and cleaning up again.
 2. Two decisions for the owner: fix alert_worker._dispatch (same dispose-after-try
    flaw, deliberately untouched) and whether to chain process_lap -> prediction
    instead of the retry.
 3. V2 (historical calibration of probability_pit_now_gains_position, with V6 folded
    in) — not started; needs owner approval. The 0.5 alert threshold and the gate
    constants are not derived from outcomes.
 4. The lead-lap gap-only fallback work — only if V5 shows F1's Position field does
    not stream on the live socket. Both V5 questions can only be answered by the
    next real race with the stack running (recording is on by default).

Working rules for this project (also in the user's CLAUDE.md files): do not run any
git command — the owner commits, pushes and opens PRs; state a brief plan and wait
for approval before code touching more than 2 files, and confirm between checkpoints
when a task is long; ask before adding any dependency; no debug print (use logging),
no TODO comments, no bare `except Exception`, type hints everywhere; if an error is
not resolved in 2 attempts, stop and show what was tried, the exact error, the likely
cause and two options. Never run the shadow race while a real live race is running.

Environment notes: Windows + Git Bash; the Docker stack is docker-compose in
infra/docker (containers docker-worker-1, docker-redis-1, docker-postgres-1);
the worker mounts backend/, so `docker restart docker-worker-1` picks up code changes
(recreate with `--env-file .env` only when compose settings change); Celery runs
`--pool=solo` on three queues; avoid backslashes in inline shell/Python heredocs (the
shell layer mangles them); use MSYS_NO_PATHCONV=1 for docker exec paths. Checks to run
before saying anything is done: `ruff check`, `ruff format --check`,
`mypy backend/ --strict`, `pytest backend/tests/unit -m unit` (648 at the end of the
last session) and the integration tests that touch the worker/prediction pipeline.

First, report back in a few lines what you understood the state to be and what you
propose to do first, and wait for approval.
```

### Superseded notes and the original anchor (kept as history)

> **Note (2026-09-19): all five issues are now fixed.** The prompt below is
> kept only as history of how the investigation was framed; a session opening
> this document should start from section 0c, then 7b (what was done) and 7c
> (what remains: V5, V3, V2 and the conditional lead-lap fallback work), and
> the "Not done / limits" notes in Issue B and Issue C's fix summaries.
>
> **Note (2026-09-18):** the prompt below is the ORIGINAL anchor, written
> before the re-verification pass. It is still broadly accurate, but its
> summaries of issues B, C and D are now superseded — B's framing was wrong
> (the defect is score bistability, and the cited ANT/VER lap-38 alert is
> not what the DB shows), C is blocked on evidence that no longer exists,
> and D spans laps 3-6 with its red flag now confirmed. A session resuming
> this work should read section 0b and each issue's dated re-verification
> note rather than relying on the summary below.

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
