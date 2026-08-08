"""One-time export of extracted circuit outlines to a local JSON file.

Run via: python -m backend.scripts.export_circuit_outlines --output PATH

Reads (name, map_geometry) from the circuits table for every circuit whose
map_geometry has been populated (see extract_circuit_outlines.py) and writes
it to a JSON file — a portable, name-keyed bridge for seed_circuit_outlines.py
to apply to another environment (e.g. Supabase) whose circuit UUIDs don't
match this database's. seed_circuits.py generates a fresh uuid.uuid4() per
environment it's run against and is itself idempotent by matching on name,
not id — so a circuit's id differs between local and Supabase, and name is
the only key that survives the transfer.

Not committed to the repo — same convention as export_training_data.py's
parquet output (uploaded to S3, not version controlled). --output has no
default on purpose, to avoid ever accidentally writing into the repo tree.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.race import Circuit

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def export_outlines(output_path: Path) -> None:
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with session_factory() as db:
        query = select(Circuit.name, Circuit.map_geometry).where(Circuit.map_geometry.is_not(None))
        rows = (await db.execute(query)).all()

    entries = [{"name": name, "map_geometry": geometry} for name, geometry in rows]
    output_path.write_text(json.dumps(entries, indent=2))
    logger.info("Exported %d circuit outline(s) to %s", len(entries), output_path)

    await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export circuit map_geometry to a JSON file.")
    parser.add_argument(
        "--output", type=Path, required=True, help="Output JSON file path (e.g. a scratch dir)"
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(export_outlines(args.output))


if __name__ == "__main__":
    main()
