"""Threat evaluation and alert dispatch.

evaluate_threats reads undercut_score directly off the StrategyPrediction
table (populated by workers/prediction_worker.py's Celery pipeline) instead
of calling strategy_service.get_undercut_score — CLAUDE.md forbids services/
importing other services/ modules, so cross-service data flows through the DB
(populated by a worker) rather than a direct import. "For all driver pairs"
is interpreted as track-position-adjacent pairs (trailing driver vs. the car
immediately ahead), matching schemas/strategy_schema.py's UndercutThreatResponse
shape (driver_ahead + threat_score) and how undercuts actually work in racing —
you only realistically threaten the car directly ahead of you, not arbitrary
pairs.

Known limitation: prediction_worker.py currently hardcodes undercut_score to
0.0 (see its _run_inference docstring — a Day 6/7 placeholder pending the
opponent-relative simulation logic strategy_service.py now provides). So
evaluate_threats will not fire real undercut alerts until a future day wires
prediction_worker to call strategy_service.get_undercut_score and persist a
real value. Not fixed here — prediction_worker.py wasn't part of today's spec.

dispatch_alert writes the Alert DB record and publishes to a new
f1:alerts:{session_id} pub/sub channel for WebSocket delivery — it does not
send FCM. FCM delivery already lives in workers/alert_worker.py (Day 6) and
must not be duplicated here. f1:alerts:{session_id} has no consumer yet (no
WS alerts endpoint exists in CLAUDE.md's API list until a later day) — same
"wired for later, not yet connected" pattern as the fcm_token gap documented
in CLAUDE.md's Deferred Schema Changes.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.models.user import Alert, Subscription
from backend.schemas.alert_schema import AlertCreate, AlertType

logger = logging.getLogger(__name__)

UNDERCUT_ALERT_THRESHOLD = 0.5


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

    Intended to run after each lap is processed. For each trailing/ahead pair in
    current running order, dispatches an UNDERCUT_THREAT alert to subscribers of
    the trailing driver if their undercut_score exceeds UNDERCUT_ALERT_THRESHOLD.

    Args:
        db: Async DB session.
        redis_client: Redis client, forwarded to dispatch_alert.
        session_id: Session to evaluate.
    Returns:
        The alert payloads that were dispatched (empty if none crossed threshold).
    """
    positions = await _latest_positions(db, session_id)
    scores = await _latest_undercut_scores(db, session_id)

    alert_type = AlertType.UNDERCUT_THREAT
    dispatched: list[dict[str, Any]] = []
    for trailing, ahead in zip(positions[1:], positions[:-1], strict=True):
        score = scores.get(trailing.driver_id)
        if score is None or score <= UNDERCUT_ALERT_THRESHOLD:
            continue

        user_ids = await _subscribed_user_ids(db, trailing.driver_id, alert_type)
        if not user_ids:
            continue

        payload = {
            "session_id": str(session_id),
            "driver_id": str(trailing.driver_id),
            "message": (
                f"Undercut threat: driver {trailing.driver_id} on driver {ahead.driver_id} "
                f"({score:.0%})"
            ),
        }
        alerts = await dispatch_alert(db, redis_client, user_ids, alert_type, payload)
        dispatched.extend(alerts)

    return dispatched


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
