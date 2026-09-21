"""Offline re-scoring of a live-ingested race's undercut scores: old gaps vs live gaps.

docs/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue B: for a live
session the undercut score was computed from summed lap times, which are wrong
(lap 1 has no recorded time, so every gap that opened on lap 1 is missing). CP3
moved live sessions onto F1's own live standings. This script measures what
that changes on a real race already in the database, with NO database writes.

For every stored StrategyPrediction it produces three scores:
  stored     - what the race actually recorded (models as of race day).
  old gaps   - CURRENT models, the old target (stored lap_data.position order,
               retirees included) and the old summed-lap-time deficit.
  live gaps  - CURRENT models, the target and deficit from the standings the
               (CP2-fixed) ingestor publishes while replaying F1's archived feed.
"old gaps" vs "live gaps" hold the models constant, so the difference between
them is purely the gap/target change. The real strategy_service functions do
the maths (state and cumulative-time reads are substituted with as-of-that-lap
values); only the inputs are swapped.

It also replays alert_service.evaluate_threats' rules (score > 0.5, one 60s
claim per trailing/ahead pair, only the subscriber's drivers) over each
variant, first checking the replay reproduces the alerts the race really sent.

Read-only: SELECTs against the database, reads S3 model artifacts, downloads
F1's archived feed through FastF1. Run via:
    python -m backend.scripts.evaluate_undercut_live_gaps
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import logging
import re
import statistics
import uuid
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.core.exceptions import ModelNotLoadedError
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.models.user import Alert, Subscription
from backend.scripts import verify_live_feed_archive as archive_harness
from backend.services import alert_service, strategy_service

logger = logging.getLogger(__name__)

# Italian GP 2026 (Monza), round 13, race session — the live-ingested race the
# issue was found on.
DEFAULT_SESSION_ID = "3ddc84bd-f10e-4870-9e98-631d79695beb"
DEFAULT_SEASON = 2026
DEFAULT_ROUND = 13

# A car whose final lap is below this share of the race distance is a retiree.
_RETIREE_LAP_SHARE = 0.95
_SATURATED_HIGH = 0.999
_SATURATED_LOW = 0.001
_FLIP_JUMP = 0.9
_ALERT_MESSAGE = re.compile(r"Undercut threat: (\w+) on (\w+) \((\d+)%\)")


# --- data ---


@dataclass(frozen=True)
class LapRow:
    position: int | None
    compound: str | None
    tyre_age_laps: int
    lap_time_seconds: float | None
    created_at: datetime


@dataclass(frozen=True)
class Prediction:
    code: str
    lap: int
    predicted_at: datetime
    stored_score: float


@dataclass
class RaceData:
    code_by_id: dict[uuid.UUID, str]
    id_by_code: dict[str, uuid.UUID]
    laps: dict[tuple[str, int], LapRow]
    lap_numbers: dict[str, list[int]]
    predictions: list[Prediction]
    circuit_id: uuid.UUID
    circuit_name: str
    total_laps: int
    subscribed: set[str]
    real_alerts: list[tuple[datetime, str]]
    cumulative_time: dict[str, dict[int, float]] = field(default_factory=dict)

    def latest_lap_at_or_before(self, code: str, lap: int) -> int | None:
        numbers = self.lap_numbers.get(code, [])
        i = bisect.bisect_right(numbers, lap)
        return numbers[i - 1] if i else None

    def summed_time(self, code: str, up_to_lap: int) -> float:
        """SUM(lap_time_seconds) through up_to_lap — the old code's live-session reading."""
        lap = self.latest_lap_at_or_before(code, up_to_lap)
        return self.cumulative_time[code][lap] if lap is not None else 0.0


async def load_race_data(session_id: uuid.UUID) -> RaceData:
    engine = get_engine()
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        drivers = (await db.execute(select(Driver.id, Driver.code))).all()
        lap_rows = (
            await db.execute(
                select(
                    LapData.driver_id,
                    LapData.lap_number,
                    LapData.position,
                    LapData.compound,
                    LapData.tyre_age_laps,
                    LapData.lap_time_seconds,
                    LapData.created_at,
                ).where(LapData.session_id == session_id)
            )
        ).all()
        prediction_rows = (
            await db.execute(
                select(
                    StrategyPrediction.driver_id,
                    StrategyPrediction.lap_number,
                    StrategyPrediction.predicted_at,
                    StrategyPrediction.undercut_score,
                )
                .where(StrategyPrediction.session_id == session_id)
                .order_by(StrategyPrediction.predicted_at)
            )
        ).all()
        circuit_id, circuit_name = (
            await db.execute(
                select(Race.circuit_id, Circuit.name)
                .join(SessionModel, SessionModel.race_id == Race.id)
                .join(Circuit, Race.circuit_id == Circuit.id)
                .where(SessionModel.id == session_id)
            )
        ).one()
        subscription_driver_ids = (
            (await db.execute(select(Subscription.driver_ids))).scalars().all()
        )
        alert_rows = (
            await db.execute(
                select(Alert.triggered_at, Alert.message)
                .where(Alert.session_id == session_id)
                .order_by(Alert.triggered_at)
            )
        ).all()
    await engine.dispose()

    code_by_id = {row.id: row.code for row in drivers}
    laps: dict[tuple[str, int], LapRow] = {}
    for row in lap_rows:
        laps[(code_by_id[row.driver_id], row.lap_number)] = LapRow(
            row.position, row.compound, row.tyre_age_laps, row.lap_time_seconds, row.created_at
        )
    lap_numbers: dict[str, list[int]] = defaultdict(list)
    for code, lap in laps:
        lap_numbers[code].append(lap)
    for numbers in lap_numbers.values():
        numbers.sort()

    cumulative: dict[str, dict[int, float]] = {}
    for code, numbers in lap_numbers.items():
        running = 0.0
        cumulative[code] = {}
        for lap in numbers:
            running += laps[(code, lap)].lap_time_seconds or 0.0
            cumulative[code][lap] = running

    subscribed_ids = {str(driver_id) for ids in subscription_driver_ids for driver_id in ids}
    return RaceData(
        code_by_id=code_by_id,
        id_by_code={code: driver_id for driver_id, code in code_by_id.items()},
        laps=laps,
        lap_numbers=dict(lap_numbers),
        predictions=[
            Prediction(code_by_id[r.driver_id], r.lap_number, r.predicted_at, r.undercut_score)
            for r in prediction_rows
            if r.lap_number is not None
        ],
        circuit_id=circuit_id,
        circuit_name=circuit_name,
        total_laps=max(lap for _, lap in laps),
        subscribed={
            code for driver_id, code in code_by_id.items() if str(driver_id) in subscribed_ids
        },
        real_alerts=[(row.triggered_at, row.message) for row in alert_rows],
        cumulative_time=cumulative,
    )


def capture_live_towers(
    season: int, round_number: int
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """Replay F1's archived feed through the ingestor; keep the published standings
    from the moment each driver first completes each lap (when its prediction ran)."""
    towers: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def _keep(entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            towers.setdefault((str(entry["driver_id"]), int(entry["lap_number"])), entries)

    archive = archive_harness.fetch_archive(season, round_number, "R")
    archive_harness.replay(archive, on_publish=_keep)
    return towers


# --- rescoring through the real strategy_service maths ---


class _StubRedis:
    """Serves one fixed value for any key — the live gaps payload, or nothing."""

    def __init__(self, payload: str | None) -> None:
        self._payload = payload

    async def get(self, key: str) -> str | None:
        return self._payload


@dataclass
class _Context:
    """What the patched strategy_service reads for the prediction being rescored."""

    states: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    up_to: dict[uuid.UUID, int] = field(default_factory=dict)
    seed: int = 0


@dataclass
class RowResult:
    prediction: Prediction
    old_target: str | None = None
    old_deficit: float | None = None
    old_score: float = 0.0
    live_target: str | None = None
    live_deficit: float | None = None
    live_from_feed: bool = False
    live_score: float = 0.0
    old_road_gap: float | None = None
    live_road_gap: float | None = None


def _state(data: RaceData, code: str, lap: int) -> dict[str, Any] | None:
    latest = data.latest_lap_at_or_before(code, lap)
    if latest is None:
        return None
    row = data.laps[(code, latest)]
    return {
        "lap_number": latest,
        "compound": row.compound,
        "tyre_age_laps": row.tyre_age_laps,
        "position": row.position,
        "total_laps": data.total_laps,
        "circuit_id": data.circuit_id,
        "circuit_name": data.circuit_name,
    }


def old_target(data: RaceData, code: str, lap: int) -> str | None:
    """The car ahead as prediction_worker._resolve_position_context picked it on the day:
    each driver's latest stored lap_data row at or before `lap`, ordered by position."""
    field_order: list[tuple[int, str]] = []
    for other in data.lap_numbers:
        latest = data.latest_lap_at_or_before(other, lap)
        if latest is None:
            continue
        position = data.laps[(other, latest)].position
        if position is not None:
            field_order.append((position, other))
    field_order.sort()
    codes = [c for _, c in field_order]
    if code not in codes or codes.index(code) == 0:
        return None
    return codes[codes.index(code) - 1]


def live_target(entries: list[dict[str, Any]], code: str) -> str | None:
    ordered = sorted(entries, key=lambda e: e["position"])
    codes = [str(e["driver_id"]) for e in ordered]
    if code not in codes or codes.index(code) == 0:
        return None
    return codes[codes.index(code) - 1]


def _road_gap(data: RaceData, code: str, target: str | None, lap: int) -> float | None:
    """Seconds by which `code` crossed the line after `target` on `lap` (positive = behind)."""
    if target is None or (code, lap) not in data.laps or (target, lap) not in data.laps:
        return None
    return (data.laps[(code, lap)].created_at - data.laps[(target, lap)].created_at).total_seconds()


async def rescore(
    data: RaceData,
    towers: dict[tuple[str, int], list[dict[str, Any]]],
    session_id: uuid.UUID,
    season: int,
    round_number: int,
) -> list[RowResult]:
    ctx = _Context()
    real_default_rng = np.random.default_rng

    async def fake_state(db: Any, sid: uuid.UUID, driver_id: uuid.UUID) -> dict[str, Any]:
        state = ctx.states[driver_id]
        ctx.up_to[driver_id] = int(state["lap_number"])
        return state

    async def fake_cumulative(
        db: Any, sid: uuid.UUID, driver_id: uuid.UUID, up_to_lap: int
    ) -> float:
        return data.summed_time(data.code_by_id[driver_id], up_to_lap)

    async def probability(client: _StubRedis, driver: str, target: str, lap: int) -> float:
        d_state, t_state = _state(data, driver, lap), _state(data, target, lap)
        if d_state is None or t_state is None:
            return 0.0
        ctx.states = {data.id_by_code[driver]: d_state, data.id_by_code[target]: t_state}
        ctx.seed = zlib.crc32(f"{driver}:{lap}".encode())
        try:
            result = await strategy_service._undercut_overcut_probability(
                client,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                season,
                round_number,
                session_id,
                data.id_by_code[driver],
                data.id_by_code[target],
            )
        except ModelNotLoadedError:
            return 0.0
        return float(result["probability_pit_now_gains_position"])

    results: list[RowResult] = []
    no_live_payload = _StubRedis(None)
    with (
        patch.object(strategy_service, "_current_state", fake_state),
        patch.object(strategy_service, "_cumulative_race_time", fake_cumulative),
        # Same noise draws for the old and live variant of one prediction, so the
        # comparison is not muddied by two independent Monte Carlo runs.
        patch.object(np.random, "default_rng", lambda *a, **k: real_default_rng(ctx.seed)),
    ):
        for n, prediction in enumerate(data.predictions, start=1):
            row = RowResult(prediction)
            code, lap = prediction.code, prediction.lap

            row.old_target = old_target(data, code, lap)
            if row.old_target is not None:
                d_state, t_state = _state(data, code, lap), _state(data, row.old_target, lap)
                if d_state is not None and t_state is not None:
                    row.old_deficit = data.summed_time(
                        code, d_state["lap_number"]
                    ) - data.summed_time(row.old_target, t_state["lap_number"])
                row.old_score = await probability(no_live_payload, code, row.old_target, lap)
            row.old_road_gap = _road_gap(data, code, row.old_target, lap)

            tower = towers.get((code, lap))
            if tower is not None:
                row.live_target = live_target(tower, code)
            if row.live_target is not None and tower is not None:
                payload = archive_json(tower, data, session_id)
                row.live_deficit = await strategy_service._live_gap_deficit(
                    _StubRedis(payload),  # type: ignore[arg-type]
                    season,
                    round_number,
                    session_id,
                    data.id_by_code[code],
                    data.id_by_code[row.live_target],
                )
                row.live_from_feed = row.live_deficit is not None
                row.live_score = await probability(_StubRedis(payload), code, row.live_target, lap)
            row.live_road_gap = _road_gap(data, code, row.live_target, lap)
            results.append(row)
            if n % 200 == 0:
                logger.warning("rescored %d / %d predictions", n, len(data.predictions))
    return results


def archive_json(tower: list[dict[str, Any]], data: RaceData, session_id: uuid.UUID) -> str:
    """The published payload with driver codes swapped for the DB driver ids the service uses."""
    import json

    entries = [{**e, "driver_id": str(data.id_by_code[str(e["driver_id"])])} for e in tower]
    return json.dumps({"session_id": str(session_id), "source": "live", "gaps": entries})


# --- alert replay ---


def emulate_alerts(
    data: RaceData,
    score_of: Callable[[Prediction], float],
    position_of: Callable[[str, int], int | None],
    *,
    live_order_at: Callable[[str, int], list[str] | None] | None = None,
    apply_race_state_gates: bool = False,
) -> list[tuple[datetime, str, str, float]]:
    """alert_service.evaluate_threats' rules, run after every prediction commit.

    Positions come from each driver's latest lap row at commit time (as
    _latest_positions reads them), scores from each driver's latest prediction;
    a pair is alerted when the trailing driver is subscribed and scores above
    UNDERCUT_ALERT_THRESHOLD, at most once per UNDERCUT_ALERT_DEDUP_TTL_SECONDS.

    Args:
        data: The race.
        score_of: Score used for each prediction.
        position_of: (code, lap) -> stored position, for the DB-derived order.
        live_order_at: If given, called with the most recent lap completion
            (code, lap) and returns the live running order (codes, leader
            first) at that moment — replaces the DB-derived order, as
            alert_service._live_standing_order does for a live session.
        apply_race_state_gates: Also suppress alerts by
            alert_service._alert_suppressed_by_race_state (this race's real
            distance is known here).
    """
    lap_events = sorted((row.created_at, code, lap) for (code, lap), row in data.laps.items())
    latest_lap: dict[str, int] = {}
    latest_score: dict[str, float] = {}
    claimed_until: dict[tuple[str, str], datetime] = {}
    ttl = timedelta(seconds=alert_service.UNDERCUT_ALERT_DEDUP_TTL_SECONDS)
    alerts: list[tuple[datetime, str, str, float]] = []
    i = 0
    for prediction in data.predictions:
        now = prediction.predicted_at
        last_event: tuple[str, int] | None = None
        while i < len(lap_events) and lap_events[i][0] <= now:
            latest_lap[lap_events[i][1]] = lap_events[i][2]
            last_event = (lap_events[i][1], lap_events[i][2])
            i += 1
        latest_score[prediction.code] = score_of(prediction)

        live_order = live_order_at(*last_event) if live_order_at and last_event else None
        if live_order is None:
            by_position: list[tuple[int, str]] = []
            for code, lap in latest_lap.items():
                position = position_of(code, lap)
                if position is not None:
                    by_position.append((position, code))
            live_order = [code for _, code in sorted(by_position)]
        for ahead, trailing in zip(live_order[:-1], live_order[1:], strict=True):
            score = latest_score.get(trailing)
            if score is None or score <= alert_service.UNDERCUT_ALERT_THRESHOLD:
                continue
            if apply_race_state_gates and trailing in latest_lap:
                lap = latest_lap[trailing]
                if alert_service._alert_suppressed_by_race_state(
                    lap, data.laps[(trailing, lap)].tyre_age_laps, data.total_laps
                ):
                    continue
            if trailing not in data.subscribed:
                continue
            if claimed_until.get((trailing, ahead), now) > now:
                continue
            claimed_until[(trailing, ahead)] = now + ttl
            alerts.append((now, trailing, ahead, score))
    return alerts


def retiree_last_row(data: RaceData) -> dict[str, datetime]:
    """Last lap-row time for every car that did not run (nearly) the whole race."""
    return {
        code: data.laps[(code, numbers[-1])].created_at
        for code, numbers in data.lap_numbers.items()
        if numbers[-1] < _RETIREE_LAP_SHARE * data.total_laps
    }


# --- summaries ---


def distribution(scores: list[float]) -> dict[str, int]:
    return {
        "n": len(scores),
        ">=0.999": sum(s >= _SATURATED_HIGH for s in scores),
        "<=0.001": sum(s <= _SATURATED_LOW for s in scores),
        "in between": sum(_SATURATED_LOW < s < _SATURATED_HIGH for s in scores),
        ">0.5 (alert-eligible)": sum(s > alert_service.UNDERCUT_ALERT_THRESHOLD for s in scores),
    }


def flip_stats(results: list[RowResult], pick: Callable[[RowResult], float]) -> tuple[int, float]:
    """(consecutive-lap swings of >= 0.9, mean absolute lap-to-lap change) across drivers."""
    by_driver: dict[str, dict[int, float]] = defaultdict(dict)
    for row in results:
        by_driver[row.prediction.code][row.prediction.lap] = pick(row)
    flips, deltas = 0, []
    for series in by_driver.values():
        for lap, score in series.items():
            if lap - 1 in series:
                delta = abs(score - series[lap - 1])
                deltas.append(delta)
                flips += delta >= _FLIP_JUMP
    return flips, (sum(deltas) / len(deltas) if deltas else 0.0)


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


def _alert_pairs(alerts: list[tuple[datetime, str, str, float]]) -> Counter[str]:
    return Counter(f"{trailing} on {ahead}" for _, trailing, ahead, _ in alerts)


def report(
    data: RaceData, results: list[RowResult], towers: dict[tuple[str, int], list[dict[str, Any]]]
) -> str:
    out: list[str] = []
    n = len(results)
    out.append(
        f"{n} stored predictions, {len(data.lap_numbers)} drivers, {data.total_laps} laps; "
        f"alert subscriber follows: {sorted(data.subscribed)}"
    )

    real_pairs: Counter[str] = Counter()
    for _, message in data.real_alerts:
        match = _ALERT_MESSAGE.match(message)
        if match:
            real_pairs[f"{match.group(1)} on {match.group(2)}"] += 1
    stored_alerts = emulate_alerts(
        data, lambda p: p.stored_score, lambda code, lap: data.laps[(code, lap)].position
    )
    emulated_pairs = _alert_pairs(stored_alerts)
    out += [
        "",
        "=== Alert-replay validation (stored scores + stored positions vs. what the race sent) ===",
        f"  real alerts: {len(data.real_alerts)}   replayed: {len(stored_alerts)}   "
        f"identical per-pair counts: {real_pairs == emulated_pairs}",
    ]
    if real_pairs != emulated_pairs:
        for pair in sorted(set(real_pairs) | set(emulated_pairs)):
            if real_pairs[pair] != emulated_pairs[pair]:
                out.append(f"    {pair}: real {real_pairs[pair]}, replayed {emulated_pairs[pair]}")

    out += ["", "=== Score distribution over all predictions (no car ahead scores 0.0) ==="]
    out.append(f"  {'':24}{'stored':>10}{'old gaps':>10}{'live gaps':>10}")
    variants = {
        "stored": [r.prediction.stored_score for r in results],
        "old gaps": [r.old_score for r in results],
        "live gaps": [r.live_score for r in results],
    }
    dists = {name: distribution(scores) for name, scores in variants.items()}
    for key in dists["stored"]:
        out.append(f"  {key:24}" + "".join(f"{dists[name][key]:>10}" for name in variants))
    saturated = {name: dists[name][">=0.999"] + dists[name]["<=0.001"] for name in variants}
    out.append(
        f"  {'pinned at 0 or 1':24}"
        + "".join(f"{_pct(saturated[name], n):>10}" for name in variants)
    )
    out += ["", "=== Lap-to-lap stability (consecutive laps, same driver) ==="]
    for name, pick in (
        ("stored", lambda r: r.prediction.stored_score),
        ("old gaps", lambda r: r.old_score),
        ("live gaps", lambda r: r.live_score),
    ):
        flips, mean_delta = flip_stats(results, pick)
        out.append(f"  {name:10} swings >= 0.9: {flips:>4}   mean |change|: {mean_delta:.3f}")

    out += ["", "=== Gap and target accuracy ==="]
    old_deficits = [r.old_deficit for r in results if r.old_deficit is not None]
    live_deficits = [r.live_deficit for r in results if r.live_deficit is not None]
    old_ahead = sum(d <= 0 for d in old_deficits)
    live_ahead = sum(d <= 0 for d in live_deficits)
    out.append(
        "  requester already ahead of its target (deficit <= 0): "
        f"old {old_ahead}/{len(old_deficits)} ({_pct(old_ahead, len(old_deficits))}), "
        f"live {live_ahead}/{len(live_deficits)} ({_pct(live_ahead, len(live_deficits))})"
    )
    old_error = [
        abs(r.old_deficit - r.old_road_gap)
        for r in results
        if r.old_deficit is not None and r.old_road_gap is not None
    ]
    live_error = [
        abs(r.live_deficit - r.live_road_gap)
        for r in results
        if r.live_deficit is not None and r.live_road_gap is not None
    ]
    if old_error and live_error:
        out.append(
            "  median |deficit - on-road crossing gap| (seconds): "
            f"old {statistics.median(old_error):.2f}, live {statistics.median(live_error):.2f}"
        )

    retirees = retiree_last_row(data)
    target_getters: list[tuple[str, Callable[[RowResult], str | None]]] = [
        ("old", lambda r: r.old_target),
        ("live", lambda r: r.live_target),
    ]
    for label, target_of in target_getters:
        stopped = Counter(
            target_of(r)
            for r in results
            if target_of(r) in retirees and r.prediction.predicted_at >= retirees[str(target_of(r))]
        )
        out.append(
            f"  {label} target was a car that had already stopped completing laps: "
            f"{sum(stopped.values())} {dict(stopped) if stopped else ''}"
        )
    both = [r for r in results if r.old_target is not None and r.live_target is not None]
    differing = sum(r.old_target != r.live_target for r in both)
    out.append(f"  target differs between old and live: {differing} of {len(both)} comparable rows")
    unavailable = sum(r.live_target is not None and not r.live_from_feed for r in results)
    out.append(f"  live deficit unavailable (lapped/absent -> falls back to summed): {unavailable}")

    out += ["", "=== The case that started this: VER, lap 30 ==="]
    for r in results:
        if r.prediction.code == "VER" and r.prediction.lap in (29, 30, 31):
            out.append(
                f"  lap {r.prediction.lap}: stored {r.prediction.stored_score:.3f} | "
                f"old gaps {r.old_score:.3f} (target {r.old_target}, deficit "
                f"{'n/a' if r.old_deficit is None else f'{r.old_deficit:+.2f}s'}) | "
                f"live gaps {r.live_score:.3f} (target {r.live_target}, deficit "
                f"{'n/a' if r.live_deficit is None else f'{r.live_deficit:+.2f}s'})"
            )

    score_old = {(r.prediction.code, r.prediction.lap): r.old_score for r in results}
    score_live = {(r.prediction.code, r.prediction.lap): r.live_score for r in results}

    def live_position(code: str, lap: int) -> int | None:
        tower = towers.get((code, lap))
        if tower is None:
            return None
        return next((int(e["position"]) for e in tower if str(e["driver_id"]) == code), None)

    def live_order(code: str, lap: int) -> list[str] | None:
        tower = towers.get((code, lap))
        if tower is None:
            return None
        return [str(e["driver_id"]) for e in sorted(tower, key=lambda e: e["position"])]

    def live_score_of(p: Prediction) -> float:
        return score_live[(p.code, p.lap)]

    scenarios = {
        "stored (as sent)": stored_alerts,
        "old gaps, current models": emulate_alerts(
            data, lambda p: score_old[(p.code, p.lap)], lambda c, lap: data.laps[(c, lap)].position
        ),
        "live gaps, alert pairing from stored rows (CP3 only)": emulate_alerts(
            data, live_score_of, live_position
        ),
        "live gaps + live-standings pairing": emulate_alerts(
            data, live_score_of, live_position, live_order_at=live_order
        ),
        "live gaps + live pairing + race-state gates (CP5)": emulate_alerts(
            data,
            live_score_of,
            live_position,
            live_order_at=live_order,
            apply_race_state_gates=True,
        ),
    }
    out += ["", "=== Alerts (single subscriber: VER + ALO; 60s dedup per pair) ==="]
    for name, alerts in scenarios.items():
        ghost = [
            a
            for a in alerts
            if (a[1] in retirees and a[0] >= retirees[a[1]])
            or (a[2] in retirees and a[0] >= retirees[a[2]])
        ]
        out.append(
            f"  {name}: {len(alerts)} alert(s); involving a car already stopped: {len(ghost)}"
        )
        out.append(
            "    "
            + ", ".join(f"{pair} x{count}" for pair, count in _alert_pairs(alerts).most_common())
        )
    return "\n".join(out)


async def main_async(args: argparse.Namespace) -> None:
    session_id = uuid.UUID(args.session_id)
    logger.warning("Loading race data (read-only)...")
    data = await load_race_data(session_id)
    logger.warning("Replaying F1's archived feed through the ingestor...")
    towers = capture_live_towers(args.season, args.round_number)
    logger.warning("Loading models (S3) and rescoring %d predictions...", len(data.predictions))
    strategy_service._load_models()
    results = await rescore(data, towers, session_id, args.season, args.round_number)
    print(report(data, results, towers))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--round", type=int, default=DEFAULT_ROUND, dest="round_number")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    main()
