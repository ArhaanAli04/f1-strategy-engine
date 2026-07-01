"""Seed all 24 2024-season F1 circuits into the circuits table.

Data sourced from official FIA/F1 circuit guides and race reports.
Run via: make seed-circuits
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.race import Circuit

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# (name, country, track_length_km, lap_record_seconds)
# lap_record_seconds: fastest race lap recorded in competition at this circuit.
# None for Las Vegas — insufficient historical data (first held 2023).
_CIRCUITS: list[tuple[str, str, float, float | None]] = [
    ("Bahrain International Circuit", "Bahrain", 5.412, 92.877),
    ("Jeddah Corniche Circuit", "Saudi Arabia", 6.174, 90.734),
    ("Albert Park Circuit", "Australia", 5.278, 80.235),
    ("Suzuka Circuit", "Japan", 5.807, 91.846),
    ("Shanghai International Circuit", "China", 5.451, 96.093),
    ("Miami International Autodrome", "United States", 5.412, 90.584),
    ("Autodromo Enzo e Dino Ferrari", "Italy", 4.909, 75.476),
    ("Circuit de Monaco", "Monaco", 3.337, 74.260),
    ("Circuit de Barcelona-Catalunya", "Spain", 4.657, 79.252),
    ("Circuit Gilles Villeneuve", "Canada", 4.361, 73.078),
    ("Red Bull Ring", "Austria", 4.318, 64.736),
    ("Silverstone Circuit", "United Kingdom", 5.891, 87.097),
    ("Hungaroring", "Hungary", 4.381, 76.627),
    ("Circuit de Spa-Francorchamps", "Belgium", 7.004, 105.829),
    ("Circuit Zandvoort", "Netherlands", 4.259, 72.097),
    ("Autodromo Nazionale Monza", "Italy", 5.793, 80.827),
    ("Baku City Circuit", "Azerbaijan", 6.003, 105.491),
    ("Marina Bay Street Circuit", "Singapore", 5.063, 95.019),
    ("Circuit of the Americas", "United States", 5.513, 95.395),
    ("Autodromo Hermanos Rodriguez", "Mexico", 4.304, 79.246),
    ("Autodromo Jose Carlos Pace", "Brazil", 4.309, 71.168),
    ("Las Vegas Strip Circuit", "United States", 6.201, None),
    ("Lusail International Circuit", "Qatar", 5.380, 83.196),
    ("Yas Marina Circuit", "Abu Dhabi", 5.281, 87.789),
]


async def seed() -> None:
    """Insert any missing circuits into the DB, skipping rows that already exist."""
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with session_factory() as session:
        result = await session.execute(select(Circuit.name))
        existing: set[str] = set(result.scalars())

        if existing:
            logger.info(
                "circuits table already has %d row(s) — skipping duplicates",
                len(existing),
            )

        new_circuits: list[Circuit] = [
            Circuit(
                id=uuid.uuid4(),
                name=name,
                country=country,
                track_length_km=track_length_km,
                lap_record_seconds=lap_record_seconds,
            )
            for name, country, track_length_km, lap_record_seconds in _CIRCUITS
            if name not in existing
        ]

        if new_circuits:
            session.add_all(new_circuits)
            await session.commit()
            logger.info("Inserted %d circuit(s)", len(new_circuits))
        else:
            logger.info("Nothing to insert — all %d circuits already present", len(_CIRCUITS))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
