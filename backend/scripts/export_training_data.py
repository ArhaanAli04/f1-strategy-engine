"""One-time/manual export of the 2018-2025 base training corpus to S3 as Parquet.

train-models.yml's CI runner has no local Postgres and no FastF1 cache (see Day 21
Deferred Wiring notes in CLAUDE.md). This script decouples the static, unchanging
part of the training set — 2018-2025, already fully ingested — from that constraint
by dumping it once to S3 as Parquet. Run this locally against whatever Postgres
already holds the historical ingestion (local Docker today; Supabase once Day 23
lands) — it does not run in CI.

retrain_incremental.py (the CI entrypoint) downloads this export from S3 and adds
only the current season's completed rounds on top of it, fetched directly from
FastF1 (no DB needed for that part).

Only needs re-running if the 2018-2025 historical corpus itself changes (e.g. a
backfill fixes a previously-missing circuit) — not on any regular schedule.

Run via: python -m backend.scripts.export_training_data
"""

import asyncio
import logging
from pathlib import Path

from backend.core.config import get_aws_settings
from backend.core.database import get_engine
from backend.scripts.train_models import (
    HOLDOUT_SEASON,
    TRAIN_SEASON_START,
    fetch_laps_from_db,
    fetch_stints_from_db,
    s3_client,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

EXPORT_DIR = Path("training_data_export")
S3_PREFIX = "training-data/base"


async def export_base_corpus() -> None:
    EXPORT_DIR.mkdir(exist_ok=True)

    logger.info(
        "Fetching laps and stints (%d-%d) from Postgres...", TRAIN_SEASON_START, HOLDOUT_SEASON
    )
    laps = await fetch_laps_from_db()
    stints = await fetch_stints_from_db()
    await get_engine().dispose()

    # session_id/driver_id are UUID objects — pyarrow can't serialize those directly,
    # and callers only ever use them as opaque grouping/encoding keys, never as UUIDs.
    laps["session_id"] = laps["session_id"].astype(str)
    laps["driver_id"] = laps["driver_id"].astype(str)
    stints["session_id"] = stints["session_id"].astype(str)
    stints["driver_id"] = stints["driver_id"].astype(str)

    laps_path = EXPORT_DIR / "laps.parquet"
    stints_path = EXPORT_DIR / "stints.parquet"
    laps.to_parquet(laps_path, index=False)
    stints.to_parquet(stints_path, index=False)
    logger.info(
        "Wrote %d lap row(s) to %s, %d stint row(s) to %s",
        len(laps),
        laps_path,
        len(stints),
        stints_path,
    )

    client = s3_client()
    bucket = get_aws_settings().aws_bucket_name
    client.upload_file(str(laps_path), bucket, f"{S3_PREFIX}/laps.parquet")
    client.upload_file(str(stints_path), bucket, f"{S3_PREFIX}/stints.parquet")
    logger.info("Uploaded to s3://%s/%s/", bucket, S3_PREFIX)


def main() -> None:
    asyncio.run(export_base_corpus())


if __name__ == "__main__":
    main()
