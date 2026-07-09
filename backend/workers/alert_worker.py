"""Celery task + Redis pub/sub bridge for dispatching strategy alerts via FCM."""

import asyncio
import json
import logging
import uuid
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.core.config import get_app_settings, get_redis_settings
from backend.core.database import get_engine
from backend.models.user import Subscription, User
from backend.schemas.alert_schema import AlertType
from backend.workers.celery_app import app

logger = logging.getLogger(__name__)

_PIT_PROBABILITY_ALERT_THRESHOLD = 0.5

_session_factory: async_sessionmaker[AsyncSession] | None = None
_firebase_app: Any = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def _get_firebase_app() -> Any | None:
    """Lazily initialise the firebase-admin app, or return None if unconfigured.

    Args:
        None.
    Returns:
        The initialised firebase_admin App, or None if no credentials are configured.
    """
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    credentials_path = get_app_settings().firebase_credentials_path
    if not credentials_path:
        logger.warning("firebase_credentials_path not set — FCM push disabled")
        return None

    import firebase_admin
    from firebase_admin import credentials

    _firebase_app = firebase_admin.initialize_app(credentials.Certificate(credentials_path))
    return _firebase_app


def _matches(subscription: Subscription, driver_id: uuid.UUID, alert_type: str) -> bool:
    return str(driver_id) in (subscription.driver_ids or []) and alert_type in (
        subscription.alert_types or []
    )


def _send_fcm(user: User, alert_type: str, prediction: dict[str, Any]) -> None:
    """Send one FCM push notification to a subscribed user, if possible.

    Args:
        user: The subscribed user to notify.
        alert_type: The AlertType value that triggered this dispatch.
        prediction: Prediction payload published on f1:predictions:{session_id}.
    Returns:
        None.
    """
    firebase_app = _get_firebase_app()
    # User has no device-token column yet — add one via a future migration
    # before push delivery can actually work. Until then this safely no-ops
    # while still exercising the full subscription-matching + dispatch path.
    token = getattr(user, "fcm_token", None)
    if firebase_app is None or token is None:
        logger.info("Skipping FCM push for user %s (no app/token configured)", user.id)
        return

    from firebase_admin import messaging

    message = messaging.Message(
        token=token,
        notification=messaging.Notification(
            title=alert_type.replace("_", " ").title(),
            body=(
                f"Driver {prediction.get('driver_id')}: "
                f"pit probability {prediction.get('pit_probability', 0):.0%}"
            ),
        ),
        data={k: str(v) for k, v in prediction.items()},
    )
    messaging.send(message, app=firebase_app)


async def _dispatch(prediction: dict[str, Any]) -> None:
    driver_id = uuid.UUID(str(prediction["driver_id"]))
    if prediction.get("pit_probability", 0) < _PIT_PROBABILITY_ALERT_THRESHOLD:
        return
    alert_type = AlertType.PIT_WINDOW_OPEN

    session_factory = _get_session_factory()
    async with session_factory() as db:
        result = await db.execute(select(Subscription).options(selectinload(Subscription.user)))
        subscriptions = result.scalars().all()

    # See telemetry_worker._persist_lap for why this dispose is required.
    await get_engine().dispose()

    for subscription in subscriptions:
        if _matches(subscription, driver_id, alert_type.value):
            _send_fcm(subscription.user, alert_type.value, prediction)


@app.task(name="dispatch_alert")  # type: ignore[untyped-decorator]
def dispatch_alert(prediction: dict[str, Any]) -> None:
    """Match a published prediction against user Subscriptions and send FCM pushes.

    Args:
        prediction: Prediction payload published on f1:predictions:{session_id}.
    Returns:
        None.
    """
    asyncio.run(_dispatch(prediction))


def listen_for_predictions() -> None:
    """Blocking Redis pub/sub listener bridging predictions to alert_queue.

    Subscribes to f1:predictions:* and enqueues a dispatch_alert task per
    message, keeping FCM I/O off the pub/sub listener thread. Run this as its
    own standalone process (`python -m backend.workers.alert_worker`),
    separate from the Celery worker processes that consume alert_queue.

    Args:
        None.
    Returns:
        None. Runs until interrupted.
    """
    client = redis.Redis.from_url(get_redis_settings().redis_url, decode_responses=True)
    pubsub = client.pubsub()
    pubsub.psubscribe("f1:predictions:*")

    for message in pubsub.listen():  # type: ignore[no-untyped-call]
        if message["type"] != "pmessage":
            continue
        try:
            prediction = json.loads(message["data"])
        except json.JSONDecodeError:
            logger.warning("Skipping malformed prediction message: %r", message["data"])
            continue
        dispatch_alert.delay(prediction)


if __name__ == "__main__":
    listen_for_predictions()
