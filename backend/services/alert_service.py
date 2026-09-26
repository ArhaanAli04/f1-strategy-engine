"""Threat evaluation and alert dispatch.

evaluate_threats reads undercut_score directly off the StrategyPrediction
table (populated by workers/prediction_worker.py's Celery pipeline) instead
of calling strategy_service.get_undercut_score — CLAUDE.md forbids services/
importing other services/ modules, so cross-service data flows through the DB
(populated by a worker) rather than a direct import. "For all driver pairs"
is interpreted as track-position-adjacent pairs (trailing driver vs. the car
immediately ahead) — the only pairing that matches how undercuts actually
work in racing (you only realistically threaten the car directly ahead of
you, not arbitrary pairs). Note this module's own pairing convention predates
and is independent of schemas/strategy_schema.py's UndercutThreatResponse
(driver_id considering the undercut + target_driver_id being undercut), which
strategy_service.get_undercut_score/get_undercut_for_session use instead.

evaluate_threats is called from workers/prediction_worker.py's
_persist_and_publish, once per driver's StrategyPrediction commit (real
undercut_score/overcut_score values — prediction_worker._resolve_undercut_overcut
wires strategy_service.get_undercut_score/get_overcut_score for real as of the
Day 13 fix pass; the earlier "hardcoded to 0.0" limitation this docstring used
to describe no longer applies). Because evaluate_threats re-evaluates every
track-position-adjacent pair in the whole session on every call — not just the
driver whose prediction just committed — and prediction_worker dispatches one
Celery task per driver per lap, the same threat would otherwise be
re-evaluated (and re-dispatched) roughly once per OTHER driver's lap commit
too, not once per lap. _dedup_key + UNDERCUT_ALERT_DEDUP_TTL_SECONDS below
guard dispatch_alert with a Redis SETNX/TTL claim per (session_id,
trailing_driver_id, ahead_driver_id, alert_type) — same pattern as CLAUDE.md's
Auto Race Detection dedup lock. The TTL is deliberately short (well under a
real F1 lap time) so a threat that genuinely resolves and later re-crosses the
threshold fires a fresh alert rather than being suppressed indefinitely by one
early claim.

dispatch_alert writes the Alert DB record and publishes to a new
f1:alerts:{session_id} pub/sub channel for WebSocket delivery — it does not
send FCM. FCM delivery already lives in workers/alert_worker.py (Day 6) and
must not be duplicated here — the two alert paths are deliberately kept fully
independent (different signal: pit_probability vs. undercut_score; different
trigger: Redis pub/sub listener vs. a direct call from prediction_worker;
different delivery: FCM push vs. DB row + WS publish) rather than having one
call the other. f1:alerts:{session_id} has no consumer yet (no WS alerts
endpoint exists in CLAUDE.md's API list until a later day) — same "wired for
later, not yet connected" pattern as the fcm_token gap documented in
CLAUDE.md's Deferred Schema Changes.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.exceptions import NotFoundError
from backend.models.driver import Driver
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.models.user import Alert, Subscription
from backend.schemas.alert_schema import AlertCreate, AlertResponse, AlertType
from backend.schemas.user_schema import SubscriptionCreate, SubscriptionResponse

logger = logging.getLogger(__name__)

UNDERCUT_ALERT_THRESHOLD = 0.5

# Situations where an undercut alert is not worth showing even when the score is
# high (docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue B). An
# undercut alert means "pit now and come out ahead", so:
# - a driver whose tyres are only a few laps old has just pitted — there is no
#   further stop to take. Monza 2026: 45 of the 202 alert-eligible predictions
#   were for a driver on tyres <= 3 laps old.
# - with fewer than this many laps left, a further stop is rarely worth its pit
#   loss. Monza 2026: 25 of the 202 alert-eligible predictions had < 15 laps
#   left, and none of the predictions that late scored above 0.999.
# Alerts are suppressed at or beyond these limits. The raw undercut_score is
# still stored unchanged — these gate the ALERT, not the probability.
UNDERCUT_ALERT_MIN_TYRE_AGE_LAPS = 4
UNDERCUT_ALERT_MIN_LAPS_REMAINING = 15

# Shorter than a real F1 lap (~80-100s) so a threat that persists across
# several lap-round evaluation bursts for the SAME pair isn't re-dispatched
# every time (evaluate_threats runs once per driver's prediction commit, so a
# ~20-driver field re-evaluates every pair ~20x per lap round) — but long
# enough to absorb that burst without a real, fresh next-lap re-crossing
# getting silently swallowed by a stale claim. See module docstring.
UNDERCUT_ALERT_DEDUP_TTL_SECONDS = 60


async def _latest_positions(db: AsyncSession, session_id: uuid.UUID) -> list[LapData]:
    """Latest LapData row per driver in a session, ordered by track position.

    Args:
        db: Async DB session.
        session_id: Session to read.
    Returns:
        LapData rows (position not null), ascending by position (1 = leader).
    """
    subq = (
        select(LapData.driver_id, func.max(LapData.lap_number).label("max_lap"))
        .where(LapData.session_id == session_id)
        .group_by(LapData.driver_id)
        .subquery()
    )
    join_condition = (LapData.driver_id == subq.c.driver_id) & (
        LapData.lap_number == subq.c.max_lap
    )
    query = (
        select(LapData)
        .join(subq, join_condition)
        .where(LapData.session_id == session_id, LapData.position.is_not(None))
        .order_by(LapData.position)
    )
    return list((await db.execute(query)).scalars().all())


# f1:{season}:{round}:pipeline_stats — a Redis hash of counters for what the live
# strategy pipeline actually did (which gap source the undercut maths used, which
# source the neighbours came from, alerts suppressed per gate), kept a day so it can
# be read after a race. Best-effort: a Redis error is logged and ignored, never raised.
_PIPELINE_STATS_TTL_SECONDS = 86400


async def _bump_pipeline_stat(
    client: aioredis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
    field: str,
) -> None:
    key = f"f1:{season}:{round_number}:pipeline_stats"
    try:
        await client.hincrby(key, field, 1)
        await client.expire(key, _PIPELINE_STATS_TTL_SECONDS)
    except RedisError:
        logger.debug("Could not update pipeline stat %s", field, exc_info=True)


async def _session_race_context(
    db: AsyncSession, session_id: uuid.UUID
) -> tuple[int, int, int | None] | None:
    """(season, round_number, total_laps) for a session, or None if it doesn't exist.

    total_laps is the REAL scheduled distance (Session.total_laps) and None when
    that isn't known — never the MAX(lap_number)-so-far proxy, which mid-race
    just restates how far the race has got and would make every lap look like
    "no laps remaining".

    Args:
        db: Async DB session.
        session_id: Session to read.
    Returns:
        The three values, or None.
    """
    query = (
        select(Race.season, Race.round_number, SessionModel.total_laps)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.id == session_id)
    )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        return None
    return int(row.season), int(row.round_number), row.total_laps


async def _live_standing_order(
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
    session_id: uuid.UUID,
) -> list[uuid.UUID] | None:
    """Drivers in current running order from F1's live standings, or None.

    Reads f1:{season}:{round}:gaps as written by ingest_live_session.py
    (source == "live") for THIS session. Retired and hidden cars are not in
    it. This replaces building the order from each driver's latest stored
    lap_data row, which keeps a retiree at its last recorded position for the
    rest of the race and mixes rows from different laps: on Monza 2026 that
    put a car that retired on lap 1 next to VER in the pairing for the whole
    race, producing "VER on LEC" alerts (16 of 44 in a replay with corrected
    scores).

    Args:
        redis_client: Async Redis client.
        season, round_number: Race weekend identifiers, for the key.
        session_id: Session being evaluated.
    Returns:
        Driver ids, leader first; None when there is no usable live payload
        for this session (a replay, a historical session, a missing or
        malformed key) — callers then keep the DB-derived order.
    """
    raw = await redis_client.get(f"f1:{season}:{round_number}:gaps")
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        entries = payload["gaps"]
        if payload.get("source") != "live" or payload.get("session_id") != str(session_id):
            return None
        ordered = sorted(entries, key=lambda entry: entry["position"])
        return [uuid.UUID(entry["driver_id"]) for entry in ordered]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        return None


def _alert_suppression_reason(
    lap_number: int, tyre_age_laps: int, total_laps: int | None
) -> str | None:
    """Why an undercut alert should not be shown for this trailing driver, or None.

    Args:
        lap_number: The driver's latest completed lap.
        tyre_age_laps: Age of the driver's current tyres at that lap.
        total_laps: Real scheduled race distance, or None if unknown (the
            remaining-laps limit is then not applied).
    Returns:
        "tyre_age" when the tyres are too fresh, "laps_remaining" when too few
        laps remain, otherwise None.
    """
    if tyre_age_laps < UNDERCUT_ALERT_MIN_TYRE_AGE_LAPS:
        return "tyre_age"
    if total_laps is not None and total_laps - lap_number < UNDERCUT_ALERT_MIN_LAPS_REMAINING:
        return "laps_remaining"
    return None


def _alert_suppressed_by_race_state(
    lap_number: int, tyre_age_laps: int, total_laps: int | None
) -> bool:
    """Whether an undercut alert should not be shown for this trailing driver.

    Args:
        lap_number: The driver's latest completed lap.
        tyre_age_laps: Age of the driver's current tyres at that lap.
        total_laps: Real scheduled race distance, or None if unknown (the
            remaining-laps limit is then not applied).
    Returns:
        True when the tyres are too fresh or too few laps remain.
    """
    return _alert_suppression_reason(lap_number, tyre_age_laps, total_laps) is not None


async def _latest_undercut_scores(
    db: AsyncSession, session_id: uuid.UUID
) -> dict[uuid.UUID, float]:
    """Most recent StrategyPrediction.undercut_score per driver in a session.

    Args:
        db: Async DB session.
        session_id: Session to read.
    Returns:
        Mapping of driver_id to their latest undercut_score.
    """
    latest_predicted_at = func.max(StrategyPrediction.predicted_at).label("latest")
    subq = (
        select(StrategyPrediction.driver_id, latest_predicted_at)
        .where(StrategyPrediction.session_id == session_id)
        .group_by(StrategyPrediction.driver_id)
        .subquery()
    )
    join_condition = (StrategyPrediction.driver_id == subq.c.driver_id) & (
        StrategyPrediction.predicted_at == subq.c.latest
    )
    query = select(StrategyPrediction.driver_id, StrategyPrediction.undercut_score).join(
        subq, join_condition
    )
    rows = (await db.execute(query)).all()
    return {row.driver_id: row.undercut_score for row in rows}


@dataclass(frozen=True)
class UndercutThreat:
    """A trailing car whose undercut score on the car ahead crossed the threshold."""

    trailing_driver_id: uuid.UUID
    ahead_driver_id: uuid.UUID
    score: float
    # Why the alert should not be shown ("tyre_age" / "laps_remaining"), or
    # None when it should. See _alert_suppression_reason.
    suppressed_reason: str | None


def rank_undercut_threats(
    running_order: list[uuid.UUID],
    scores: dict[uuid.UUID, float],
    race_state: dict[uuid.UUID, tuple[int, int]],
    total_laps: int | None,
) -> list[UndercutThreat]:
    """Undercut threats between track-position-adjacent cars, with no I/O.

    The one place the alert rules live, shared by the live path
    (evaluate_threats) and the replay precompute (find_undercut_threats_at_lap).

    Args:
        running_order: Driver ids, leader first.
        scores: driver_id -> undercut_score against the car ahead.
        race_state: driver_id -> (lap_number, tyre_age_laps) it is judged at.
            A driver without an entry is never suppressed (nothing to judge by).
        total_laps: Real scheduled race distance, or None if unknown.
    Returns:
        One UndercutThreat per trailing car whose score exceeds
        UNDERCUT_ALERT_THRESHOLD, in running order, suppressed ones included.
    """
    threats: list[UndercutThreat] = []
    for trailing_id, ahead_id in zip(running_order[1:], running_order[:-1], strict=True):
        score = scores.get(trailing_id)
        if score is None or score <= UNDERCUT_ALERT_THRESHOLD:
            continue
        state = race_state.get(trailing_id)
        reason = _alert_suppression_reason(*state, total_laps) if state is not None else None
        threats.append(UndercutThreat(trailing_id, ahead_id, score, reason))
    return threats


def _undercut_message(trailing_code: str, ahead_code: str, score: float) -> str:
    return f"Undercut threat: {trailing_code} on {ahead_code} ({score:.0%})"


async def _driver_codes(db: AsyncSession, driver_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Resolve driver_id -> short code (e.g. "HUL") for a set of drivers.

    Used so alert message text reads like a real timing screen ("Undercut
    threat: HUL on RUS") instead of raw UUIDs. Missing ids (shouldn't happen
    for a driver_id sourced from LapData, but defensive per this module's
    degrade-gracefully convention) are simply absent from the returned dict —
    callers fall back to str(driver_id).

    Args:
        db: Async DB session.
        driver_ids: Drivers to resolve.
    Returns:
        Mapping of driver_id to Driver.code.
    """
    if not driver_ids:
        return {}
    query = select(Driver.id, Driver.code).where(Driver.id.in_(driver_ids))
    rows = (await db.execute(query)).all()
    return {row.id: row.code for row in rows}


def _dedup_key(
    session_id: uuid.UUID,
    trailing_driver_id: uuid.UUID,
    ahead_driver_id: uuid.UUID,
    alert_type: AlertType,
) -> str:
    """Redis key claiming one (session, trailing driver, ahead driver, alert type) threat.

    Scoped to the specific pairing, not just the trailing driver — if track
    position changes and a different car becomes the relevant rival, that's a
    distinct threat worth its own alert, not something the old pairing's claim
    should suppress.
    """
    return f"f1:alerts:dedup:{session_id}:{trailing_driver_id}:{ahead_driver_id}:{alert_type.value}"


async def _subscribed_user_ids(
    db: AsyncSession, driver_id: uuid.UUID, alert_type: AlertType
) -> list[uuid.UUID]:
    query = select(Subscription.user_id).where(
        Subscription.driver_ids.contains([str(driver_id)]),
        Subscription.alert_types.contains([alert_type.value]),
    )
    return [row.user_id for row in (await db.execute(query)).all()]


async def evaluate_threats(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Check undercut threat scores for every track-position-adjacent driver pair.

    Intended to run after each driver's StrategyPrediction commits. For each
    trailing/ahead pair in current running order, dispatches an UNDERCUT_THREAT
    alert to subscribers of the trailing driver if their undercut_score exceeds
    UNDERCUT_ALERT_THRESHOLD — unless a dedup claim for that exact pairing is
    already held (see _dedup_key / UNDERCUT_ALERT_DEDUP_TTL_SECONDS), or the
    trailing driver's tyres are too fresh / too few laps remain
    (_alert_suppressed_by_race_state).

    "Current running order" comes from F1's live standings for a live session
    (_live_standing_order — retired cars are not in it) and from each driver's
    latest stored lap_data row otherwise, which is unchanged for a replayed or
    historical session.

    Args:
        db: Async DB session.
        redis_client: Redis client, forwarded to dispatch_alert and used for
            the dedup claim.
        session_id: Session to evaluate.
    Returns:
        The alert payloads that were dispatched (empty if none crossed
        threshold, or all crossings were already claimed by a recent dedup key).
    """
    positions = await _latest_positions(db, session_id)
    scores = await _latest_undercut_scores(db, session_id)

    live_order: list[uuid.UUID] | None = None
    total_laps: int | None = None
    context = await _session_race_context(db, session_id)
    if context is not None:
        season, round_number, total_laps = context
        live_order = await _live_standing_order(redis_client, season, round_number, session_id)
        await _bump_pipeline_stat(
            redis_client,
            season,
            round_number,
            "alert_order_source_live" if live_order is not None else "alert_order_source_db",
        )
    running_order = (
        live_order if live_order is not None else [position.driver_id for position in positions]
    )
    latest_state = {p.driver_id: (p.lap_number, p.tyre_age_laps) for p in positions}
    driver_codes = await _driver_codes(db, running_order)

    alert_type = AlertType.UNDERCUT_THREAT
    dispatched: list[dict[str, Any]] = []
    for threat in rank_undercut_threats(running_order, scores, latest_state, total_laps):
        trailing_id, ahead_id, score = (
            threat.trailing_driver_id,
            threat.ahead_driver_id,
            threat.score,
        )
        # Before the subscriber lookup and the dedup claim, so a suppressed
        # alert neither costs a query nor uses up the pair's claim.
        if threat.suppressed_reason is not None:
            if context is not None:
                await _bump_pipeline_stat(
                    redis_client,
                    season,
                    round_number,
                    f"alerts_suppressed_{threat.suppressed_reason}",
                )
            continue

        user_ids = await _subscribed_user_ids(db, trailing_id, alert_type)
        if not user_ids:
            continue

        claimed = await redis_client.set(
            _dedup_key(session_id, trailing_id, ahead_id, alert_type),
            "1",
            nx=True,
            ex=UNDERCUT_ALERT_DEDUP_TTL_SECONDS,
        )
        if not claimed:
            continue

        trailing_code = driver_codes.get(trailing_id, str(trailing_id))
        ahead_code = driver_codes.get(ahead_id, str(ahead_id))
        payload = {
            "session_id": str(session_id),
            "driver_id": str(trailing_id),
            "message": _undercut_message(trailing_code, ahead_code, score),
        }
        alerts = await dispatch_alert(db, redis_client, user_ids, alert_type, payload)
        dispatched.extend(alerts)
        if context is not None:
            await _bump_pipeline_stat(redis_client, season, round_number, "alerts_dispatched")

    return dispatched


@dataclass(frozen=True)
class LapUndercutAlert:
    """An undercut alert the live pipeline would raise on one lap (shape of a
    replay_alert_events row, minus its session)."""

    lap_number: int
    alert_type: str
    driver_id: uuid.UUID
    rival_driver_id: uuid.UUID
    message: str
    score: float


async def find_undercut_threats_at_lap(
    db: AsyncSession,
    session_id: uuid.UUID,
    lap_number: int,
    scores: dict[uuid.UUID, float],
) -> list[LapUndercutAlert]:
    """Undercut alerts as the field stood on one lap, for the replay precompute.

    evaluate_threats judges the field from each driver's LATEST stored lap and
    latest prediction, which for an already-ingested race is the finish. This
    judges it as of lap_number instead: running order and tyre ages come from
    that lap's lap_data rows (a car with no row on that lap, e.g. retired, is
    not in the order) and scores are the predictions made for that lap. Same
    threshold and suppression rules (rank_undercut_threats). No subscriber
    lookup, dedup, write or publish — playback turns these into Alert rows.

    Args:
        db: Async DB session.
        session_id: Session to evaluate.
        lap_number: The lap to judge the field at.
        scores: driver_id -> undercut_score predicted for this lap.
    Returns:
        One LapUndercutAlert per non-suppressed threat, in running order.
    """
    query = (
        select(LapData)
        .where(
            LapData.session_id == session_id,
            LapData.lap_number == lap_number,
            LapData.position.is_not(None),
        )
        .order_by(LapData.position)
    )
    laps = list((await db.execute(query)).scalars().all())
    running_order = [lap.driver_id for lap in laps]
    race_state = {lap.driver_id: (lap.lap_number, lap.tyre_age_laps) for lap in laps}
    context = await _session_race_context(db, session_id)
    total_laps = context[2] if context is not None else None

    threats = [
        threat
        for threat in rank_undercut_threats(running_order, scores, race_state, total_laps)
        if threat.suppressed_reason is None
    ]
    if not threats:
        return []

    driver_codes = await _driver_codes(db, running_order)
    return [
        LapUndercutAlert(
            lap_number=lap_number,
            alert_type=AlertType.UNDERCUT_THREAT.value,
            driver_id=threat.trailing_driver_id,
            rival_driver_id=threat.ahead_driver_id,
            message=_undercut_message(
                driver_codes.get(threat.trailing_driver_id, str(threat.trailing_driver_id)),
                driver_codes.get(threat.ahead_driver_id, str(threat.ahead_driver_id)),
                threat.score,
            ),
            score=threat.score,
        )
        for threat in threats
    ]


async def dispatch_alert(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    user_ids: list[uuid.UUID],
    alert_type: AlertType,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Write Alert rows and publish them for WebSocket delivery.

    Business logic only: decides nothing about FCM (see module docstring for why).

    Args:
        db: Async DB session.
        redis_client: Redis client (pub/sub publish).
        user_ids: Users to alert.
        alert_type: One of AlertType.
        payload: Must include session_id and message; may include driver_id.
    Returns:
        The created alert payloads (JSON-serialisable dicts), one per user_id.
    """
    triggered_at = datetime.now(UTC)
    session_id = uuid.UUID(str(payload["session_id"]))
    driver_id = uuid.UUID(str(payload["driver_id"])) if payload.get("driver_id") else None
    message = str(payload["message"])

    created: list[dict[str, Any]] = []
    for user_id in user_ids:
        alert_create = AlertCreate(
            user_id=user_id,
            session_id=session_id,
            alert_type=alert_type,
            driver_id=driver_id,
            message=message,
            triggered_at=triggered_at,
        )
        alert = Alert(
            id=uuid.uuid4(),
            user_id=alert_create.user_id,
            session_id=alert_create.session_id,
            alert_type=alert_type.value,
            driver_id=alert_create.driver_id,
            message=alert_create.message,
            triggered_at=alert_create.triggered_at,
        )
        db.add(alert)
        created.append({"id": str(alert.id), **alert_create.model_dump(mode="json")})

    await db.commit()

    channel = f"f1:alerts:{session_id}"
    for alert_payload in created:
        await redis_client.publish(channel, json.dumps(alert_payload))

    return created


# --- GET/PUT /alerts and /alerts/subscriptions ---


async def get_user_alerts(
    db: AsyncSession, user_id: uuid.UUID, unread: bool = False
) -> list[AlertResponse]:
    """List a user's alert history, newest first.

    Args:
        db: Async DB session.
        user_id: User whose alerts to list.
        unread: If True, only rows with read_at IS NULL (see the
            20260712_add_read_at_to_alerts migration — distinct from
            delivered_at, which tracks push/WS delivery, not user
            acknowledgement). If False, all alerts regardless of read state.
    Returns:
        AlertResponse rows ordered by triggered_at descending.
    """
    filters = [Alert.user_id == user_id]
    if unread:
        filters.append(Alert.read_at.is_(None))
    query = select(Alert).where(*filters).order_by(Alert.triggered_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return [AlertResponse.model_validate(r) for r in rows]


async def mark_alert_read(
    db: AsyncSession, user_id: uuid.UUID, alert_id: uuid.UUID
) -> AlertResponse:
    """Mark one of a user's own alerts as read.

    Args:
        db: Async DB session.
        user_id: User performing the action — scopes the lookup so a user
            can only mark their own alerts read, not anyone else's.
        alert_id: Alert to mark read.
    Returns:
        The updated AlertResponse.
    Raises:
        NotFoundError: No alert with this ID exists for this user.
    """
    query = select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id)
    alert = (await db.execute(query)).scalar_one_or_none()
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} not found")

    alert.read_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(alert)
    return AlertResponse.model_validate(alert)


async def _get_or_create_subscription(db: AsyncSession, user_id: uuid.UUID) -> Subscription:
    """Fetch a user's Subscription row, creating an empty default on first access.

    Subscription has no unique constraint on user_id (nothing in the schema
    prevents multiple rows per user) — this always operates on the
    lowest-id row for a user, and get_subscription/update_subscription both
    route through this so a repeated PUT updates the same row rather than
    inserting a new one each time.

    Args:
        db: Async DB session.
        user_id: User whose subscription row to fetch or create.
    Returns:
        The existing or newly created Subscription row.
    """
    query = select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.id)
    subscription = (await db.execute(query)).scalars().first()
    if subscription is None:
        subscription = Subscription(
            id=uuid.uuid4(), user_id=user_id, driver_ids=[], team_ids=[], alert_types=[]
        )
        db.add(subscription)
        await db.commit()
        await db.refresh(subscription)
    return subscription


async def get_subscription(db: AsyncSession, user_id: uuid.UUID) -> SubscriptionResponse:
    """Fetch a user's current alert-subscription preferences.

    Args:
        db: Async DB session.
        user_id: User whose subscription preferences to fetch.
    Returns:
        SubscriptionResponse — an empty-default row (see
        _get_or_create_subscription) if the user has never set preferences.
    """
    subscription = await _get_or_create_subscription(db, user_id)
    return SubscriptionResponse.model_validate(subscription)


async def update_subscription(
    db: AsyncSession, user_id: uuid.UUID, payload: SubscriptionCreate
) -> SubscriptionResponse:
    """Replace a user's alert-subscription preferences.

    Args:
        db: Async DB session.
        user_id: User whose preferences to update.
        payload: New driver_ids/team_ids/alert_types, replacing the existing values.
    Returns:
        SubscriptionResponse reflecting the update.
    """
    subscription = await _get_or_create_subscription(db, user_id)
    subscription.driver_ids = [str(driver_id) for driver_id in payload.driver_ids]
    subscription.team_ids = [str(team_id) for team_id in payload.team_ids]
    subscription.alert_types = list(payload.alert_types)
    await db.commit()
    await db.refresh(subscription)
    return SubscriptionResponse.model_validate(subscription)
