"""One-time extraction of circuit outline geometry from FastF1 telemetry.

Run via: python backend/scripts/extract_circuit_outlines.py [--force] [--circuit-id UUID]

For each circuit in the circuits table (skipping ones that already have
map_geometry, unless --force), finds the most recent race session in the
sessions table for that circuit, loads that session's fastest-lap position
telemetry from FastF1, rotates it into the same frame circuit_info.rotation
describes (matching the live Position.z feed's coordinate space — see
CLAUDE.md's Circuit Map Panel plan, so live driver dots line up with this
outline without per-circuit calibration), normalizes it to a "0 0 1000 1000"
SVG viewBox, and stores the result as JSONB in Circuit.map_geometry.

Run locally first against docker-compose's Postgres to verify; then set
DATABASE_URL to SUPABASE_DATABASE_URL and re-run against production — see
CLAUDE.md's Deferred Wiring note (same manual pattern as seed_circuits.py).
"""

import argparse
import asyncio
import logging
import math
import os
import uuid
from typing import Any

import fastf1
import numpy as np
import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_ml_settings
from backend.core.database import get_engine
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_TARGET_VIEWBOX_SIZE = 1000.0
_VIEWBOX_PADDING_FRACTION = 0.05
_MAX_OUTLINE_POINTS = 400


def _rotate(xy: np.ndarray, angle_degrees: float) -> np.ndarray:
    angle = np.radians(angle_degrees)
    rotation_matrix = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    result: np.ndarray = xy @ rotation_matrix
    return result


def _mirror_and_rotate(xy: np.ndarray, rotation_degrees: float) -> np.ndarray:
    """Correct FastF1's raw-X handedness, then apply circuit_info.rotation.

    Shared first step for both the outline's own position samples and
    circuit_info.corners — both are raw telemetry-frame X/Y from the same
    FastF1 coordinate space (confirmed empirically: transforming Zandvoort's
    14 corners through this exact pipeline lands every one within ~10 units
    of the nearest outline point, in the 1000x1000 viewBox), so both need
    the identical correction before centering/scaling.
    """
    corrected = xy.copy()
    corrected[:, 0] = -corrected[:, 0]
    return _rotate(corrected, rotation_degrees)


def _normalize(
    xy: np.ndarray, center: np.ndarray, scale: float, viewbox_center: float
) -> np.ndarray:
    result: np.ndarray = (xy - center) * scale + viewbox_center
    return result


def _downsample(points: np.ndarray, max_points: int) -> np.ndarray:
    if len(points) <= max_points:
        return points
    stride = math.ceil(len(points) / max_points)
    result: np.ndarray = points[::stride]
    return result


def _build_geometry(
    pos: pd.DataFrame,
    corners: pd.DataFrame,
    rotation_degrees: float,
    season: int,
    round_number: int,
) -> dict[str, Any]:
    """Rotate/normalize a lap's X/Y position samples (and corner markers) into an SVG viewBox.

    Args:
        pos: get_pos_data() output for one lap (needs X/Y columns).
        corners: circuit_info.corners (X, Y, Number, ...) — same FastF1
            coordinate frame as pos (see _mirror_and_rotate's docstring),
            transformed through the identical pipeline so turn markers land
            on the outline they're drawn against.
        rotation_degrees: circuit_info.rotation for this session — the same
            rotation the live Position.z feed's raw X/Y needs applied to
            match this outline's frame.
        season, round_number: Recorded as provenance (source), not used in
            the geometry math.
    Returns:
        JSON-serialisable dict: viewbox, points (list of [x, y] pairs,
        downsampled to at most _MAX_OUTLINE_POINTS), corners (list of
        {number, x, y}), source, and transform — the rotation/center/scale
        this function applied, so a consumer can apply the identical
        transform to raw (unrotated, unnormalized) live Position.z X/Y and
        have the result land in this same viewBox frame (see
        CircuitOutlineResponse.transform / CircuitMapPanel's applyTransform
        on the frontend).
    """
    xy: np.ndarray = pos.loc[:, ["X", "Y"]].to_numpy(dtype=float)
    rotated = _mirror_and_rotate(xy, rotation_degrees)
    rotated = _downsample(rotated, _MAX_OUTLINE_POINTS)

    mins = rotated.min(axis=0)
    maxs = rotated.max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)  # avoid div-by-zero on degenerate data
    center = mins + span / 2
    uniform_span = float(span.max())  # one scale factor for both axes -> preserves track shape
    padded_size = _TARGET_VIEWBOX_SIZE * (1 - 2 * _VIEWBOX_PADDING_FRACTION)
    scale = padded_size / uniform_span
    viewbox_center = _TARGET_VIEWBOX_SIZE / 2

    scaled = _normalize(rotated, center, scale, viewbox_center)
    points = [[round(float(x), 2), round(float(y), 2)] for x, y in scaled]

    corners_xy: np.ndarray = corners.loc[:, ["X", "Y"]].to_numpy(dtype=float)
    corners_rotated = _mirror_and_rotate(corners_xy, rotation_degrees)
    corners_scaled = _normalize(corners_rotated, center, scale, viewbox_center)
    corner_markers = [
        {"number": int(number), "x": round(float(x), 2), "y": round(float(y), 2)}
        for number, (x, y) in zip(corners["Number"].to_numpy(), corners_scaled, strict=True)
    ]

    return {
        "viewbox": f"0 0 {int(_TARGET_VIEWBOX_SIZE)} {int(_TARGET_VIEWBOX_SIZE)}",
        "points": points,
        "corners": corner_markers,
        "source": {"season": season, "round": round_number, "session_type": "R"},
        "transform": {
            "rotation_degrees": rotation_degrees,
            "center_x": float(center[0]),
            "center_y": float(center[1]),
            "scale": scale,
            "viewbox_center": viewbox_center,
        },
    }


def _extract_geometry(season: int, round_number: int) -> dict[str, Any]:
    fastf1_session = fastf1.get_session(season, round_number, "R")
    fastf1_session.load(laps=True, telemetry=True, weather=False, messages=False)

    fastest = fastf1_session.laps.pick_fastest()
    pos = fastest.get_pos_data()
    if pos is None or pos.empty:
        raise RuntimeError(f"No position telemetry for season {season} round {round_number}")

    circuit_info = fastf1_session.get_circuit_info()
    return _build_geometry(
        pos, circuit_info.corners, float(circuit_info.rotation), season, round_number
    )


async def _find_candidate_sessions(
    db: AsyncSession, circuit_id: uuid.UUID
) -> list[tuple[int, int]]:
    """Race weekends held at this circuit, most recent first — extraction candidates.

    Args:
        db: Async DB session.
        circuit_id: Circuit to find candidate race sessions for.
    Returns:
        (season, round_number) pairs, newest first.
    """
    query = (
        select(Race.season, Race.round_number)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(Race.circuit_id == circuit_id, SessionModel.session_type == "R")
        .order_by(Race.season.desc(), Race.round_number.desc())
        .distinct()
    )
    rows = (await db.execute(query)).all()
    return [(int(row[0]), int(row[1])) for row in rows]


async def _extract_for_circuit(
    session_factory: async_sessionmaker[AsyncSession], circuit: Circuit, force: bool
) -> str:
    """Extract and persist one circuit's outline.

    Args:
        session_factory: Fresh-session factory (one per DB operation, same
            pattern as ingest_historical.py's _ingest_all_rounds subprocess
            isolation rationale, minus the subprocess — one blocking FastF1
            call per circuit already serializes this loop).
        circuit: Circuit row to extract for.
        force: Re-extract even if map_geometry is already set.
    Returns:
        "extracted", "skipped" (already populated), or "failed" (no
        candidate session had usable telemetry).
    """
    if circuit.map_geometry is not None and not force:
        logger.info(
            "Skipping %s — already has map_geometry (use --force to re-extract)", circuit.name
        )
        return "skipped"

    async with session_factory() as db:
        candidates = await _find_candidate_sessions(db, circuit.id)
    if not candidates:
        logger.warning("No race sessions found in DB for %s — skipping", circuit.name)
        return "failed"

    for season, round_number in candidates:
        try:
            geometry = _extract_geometry(season, round_number)
        except Exception as exc:  # noqa: BLE001 — per-candidate skip, try the next one
            logger.warning(
                "Failed to extract outline for %s from season %d round %d: %s",
                circuit.name,
                season,
                round_number,
                exc,
            )
            continue

        async with session_factory() as db:
            await db.execute(
                update(Circuit).where(Circuit.id == circuit.id).values(map_geometry=geometry)
            )
            await db.commit()
        logger.info(
            "Extracted outline for %s from season %d round %d (%d points)",
            circuit.name,
            season,
            round_number,
            len(geometry["points"]),
        )
        return "extracted"

    logger.warning(
        "Exhausted all candidate sessions for %s — could not extract outline", circuit.name
    )
    return "failed"


async def run(force: bool, circuit_id: str | None) -> None:
    settings = get_ml_settings()
    os.makedirs(settings.fastf1_cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(settings.fastf1_cache_dir)

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        query = select(Circuit)
        if circuit_id is not None:
            query = query.where(Circuit.id == uuid.UUID(circuit_id))
        circuits = (await db.execute(query)).scalars().all()

    counts = {"extracted": 0, "skipped": 0, "failed": 0}
    for circuit in circuits:
        outcome = await _extract_for_circuit(session_factory, circuit, force)
        counts[outcome] += 1

    logger.info(
        "Done: %d extracted, %d skipped, %d failed",
        counts["extracted"],
        counts["skipped"],
        counts["failed"],
    )
    await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract circuit outline geometry from FastF1 telemetry."
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-extract even if map_geometry is already set"
    )
    parser.add_argument("--circuit-id", type=str, default=None, help="Only extract this circuit")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(run(args.force, args.circuit_id))


if __name__ == "__main__":
    main()
