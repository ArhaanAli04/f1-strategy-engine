"""Seed circuit map_geometry from a local JSON export (see export_circuit_outlines.py).

Run via: python -m backend.scripts.seed_circuit_outlines --input PATH

Matches seed_circuits.py's idiom: idempotent (safe to re-run; each row is a
plain UPDATE, not an insert), matched by circuit name rather than id — see
export_circuit_outlines.py's docstring for why id can't be used across
environments. Updates whichever DATABASE_URL is active — see CLAUDE.md's
Deferred Wiring note for the Supabase invocation pattern (env-var override
in the shell command itself, never written to .env).
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.race import Circuit

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def seed_outlines(input_path: Path) -> None:
    entries = json.loads(input_path.read_text())

    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    updated = 0
    not_found = 0
    async with session_factory() as db:
        for entry in entries:
            result = cast(
                CursorResult[Any],
                await db.execute(
                    update(Circuit)
                    .where(Circuit.name == entry["name"])
                    .values(map_geometry=entry["map_geometry"])
                ),
            )
            if result.rowcount == 0:
                logger.warning("No circuit named '%s' found — skipping", entry["name"])
                not_found += 1
            else:
                updated += 1
        await db.commit()

    logger.info("Done: %d circuit(s) updated, %d not found", updated, not_found)
    await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed circuit map_geometry from a JSON export.")
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input JSON file path (see export_circuit_outlines.py)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(seed_outlines(args.input))


if __name__ == "__main__":
    main()
