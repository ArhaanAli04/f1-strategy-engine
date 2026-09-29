"""Add a driver-code table to the production tyre-model sidecars, without retraining.

Why this exists: each tire_deg model's S3 sidecar (production/tire_deg_*.pkl.
metrics.json) maps drivers to the category code the model was trained on, keyed
by the driver UUIDs of the database it was trained from (the local one).
Production Supabase has its own UUIDs, so none of its drivers resolved and every
one silently fell back to a stand-in code. Inference now looks drivers up by
their 3-letter code first (tire_deg_model.resolve_driver_code), and training
writes driver_code_to_code itself (docs/internal/demo-deployment-plan-2026.md,
Day 3b). This one-off gives the sidecars already in production that table:

  - each UUID key is translated to its driver code using the database the models
    were trained from (--source-url-env, default DATABASE_URL = local);
  - the numbers are unchanged, so the .pkl models are not touched;
  - nothing is written unless every UUID has a code there. A sidecar whose table
    is already current is left alone, so a re-run is harmless.

--write copies each original sidecar to --archive-prefix first (our AWS user
cannot list object versions, so the archive is the rollback), then uploads the
updated one and reads it back.

Run via:
    python -m backend.scripts.backfill_sidecar_driver_codes            # dry run
    python -m backend.scripts.backfill_sidecar_driver_codes --write
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from backend.core.config import get_aws_settings
from backend.models.driver import Driver
from backend.scripts.copy_replay_precompute import asyncpg_url
from backend.scripts.train_models import COMPOUND_TO_FILENAME, s3_client

logger = logging.getLogger(__name__)

SIDECAR_PREFIX = "production"


@dataclass
class SidecarPlan:
    """What the backfill would do to one sidecar."""

    filename: str
    status: str  # "add", "current" (already has this exact table) or "blocked"
    code_table: dict[str, int] = field(default_factory=dict)
    missing_ids: list[str] = field(default_factory=list)
    problem: str | None = None


def sidecar_key(filename: str) -> str:
    return f"{SIDECAR_PREFIX}/{filename}.metrics.json"


def translate_driver_table(
    id_table: dict[str, int], codes: dict[str, str]
) -> tuple[dict[str, int], list[str]]:
    """Re-key a sidecar's driver UUID table by driver code.

    Args:
        id_table: The sidecar's driver_id_to_code (str(UUID) -> trained code).
        codes: The training database's str(UUID) -> driver code.
    Returns:
        (driver_code -> trained code, UUIDs with no code in that database).
    Raises:
        ValueError: Two UUIDs share a driver code but have different trained codes.
    """
    code_table: dict[str, int] = {}
    missing: list[str] = []
    for driver_id, trained in id_table.items():
        driver_code = codes.get(driver_id)
        if driver_code is None:
            missing.append(driver_id)
            continue
        if code_table.get(driver_code, trained) != trained:
            raise ValueError(
                f"driver code {driver_code} maps to both {code_table[driver_code]} and {trained}"
            )
        code_table[driver_code] = trained
    return code_table, missing


def plan_sidecar(filename: str, metrics: dict[str, Any], codes: dict[str, str]) -> SidecarPlan:
    """Decide what to do with one sidecar.

    Args:
        filename: Model filename, e.g. "tire_deg_medium.pkl".
        metrics: The sidecar's current contents.
        codes: The training database's str(UUID) -> driver code.
    Returns:
        The plan; status "blocked" carries the reason in problem.
    """
    id_table = metrics.get("driver_id_to_code")
    if not isinstance(id_table, dict) or not id_table:
        return SidecarPlan(filename, "blocked", problem="no driver_id_to_code table")
    try:
        code_table, missing = translate_driver_table(id_table, codes)
    except ValueError as exc:
        return SidecarPlan(filename, "blocked", problem=str(exc))
    if missing:
        return SidecarPlan(
            filename,
            "blocked",
            code_table,
            missing,
            problem=f"{len(missing)} driver UUID(s) not in the source database",
        )
    existing = metrics.get("driver_code_to_code")
    if existing == code_table:
        return SidecarPlan(filename, "current", code_table)
    if existing is not None:
        return SidecarPlan(
            filename, "blocked", code_table, problem="has a different driver_code_to_code already"
        )
    return SidecarPlan(filename, "add", code_table)


async def load_driver_codes(url: str) -> dict[str, str]:
    engine = create_async_engine(asyncpg_url(url), connect_args={"statement_cache_size": 0})
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(select(Driver.id, Driver.code))).all()
    finally:
        await engine.dispose()
    return {str(driver_id): code for driver_id, code in rows}


def run(source_url: str, write: bool, archive_prefix: str) -> int:
    codes = asyncio.run(load_driver_codes(source_url))
    client = s3_client()
    bucket = get_aws_settings().aws_bucket_name

    sidecars: dict[str, dict[str, Any]] = {}
    plans: list[SidecarPlan] = []
    for filename in COMPOUND_TO_FILENAME.values():
        body = client.get_object(Bucket=bucket, Key=sidecar_key(filename))["Body"].read()
        sidecars[filename] = json.loads(body)
        plans.append(plan_sidecar(filename, sidecars[filename], codes))

    print(f"source database: {len(codes)} drivers")
    for plan in plans:
        print(f"{plan.filename:<22} {plan.status:<8} drivers={len(plan.code_table)}")
        if plan.problem:
            print(f"    problem: {plan.problem}")
        for driver_id in plan.missing_ids:
            print(f"    no code for {driver_id}")
    if plans:
        table = next((p.code_table for p in plans if p.code_table), {})
        print("code table:", json.dumps(dict(sorted(table.items()))))

    if any(plan.status == "blocked" for plan in plans):
        print("Blocked: nothing written.")
        return 1
    to_write = [plan for plan in plans if plan.status == "add"]
    if not write:
        print(f"Dry run: {len(to_write)} sidecar(s) would be updated. Re-run with --write.")
        return 0

    for plan in to_write:
        key = sidecar_key(plan.filename)
        archive_key = f"{archive_prefix}/{key}"
        client.copy_object(
            Bucket=bucket, Key=archive_key, CopySource={"Bucket": bucket, "Key": key}
        )
        updated = dict(sidecars[plan.filename])
        updated["driver_code_to_code"] = plan.code_table
        client.put_object(Bucket=bucket, Key=key, Body=json.dumps(updated).encode("utf-8"))
        written = json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())
        unchanged = {k: v for k, v in written.items() if k != "driver_code_to_code"}
        ok = (
            written.get("driver_code_to_code") == plan.code_table
            and unchanged == sidecars[plan.filename]
        )
        print(f"{plan.filename}: archived to {archive_key}, updated, read back ok={ok}")
        if not ok:
            return 1
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-url-env",
        default="DATABASE_URL",
        help="Environment variable holding the database the models were trained from.",
    )
    parser.add_argument(
        "--write", action="store_true", help="Archive and upload (default: dry run)."
    )
    parser.add_argument(
        "--archive-prefix",
        default=f"archive/{datetime.now(UTC):%Y-%m-%d}",
        help="Where the original sidecars are copied before being replaced.",
    )
    args = parser.parse_args()
    source_url = os.environ.get(args.source_url_env)
    if not source_url:
        sys.exit(f"{args.source_url_env} is not set")
    sys.exit(run(source_url, args.write, args.archive_prefix))


if __name__ == "__main__":
    main()
