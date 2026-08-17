import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


class CircuitOutlineTransform(BaseModel):
    """Rotation/center/scale extract_circuit_outlines.py applied to build `points`.

    A consumer plotting raw (unrotated, unnormalized) live Position.z X/Y
    applies this identical transform first, so live driver dots land in the
    same viewBox frame as the outline polyline — see CircuitMapPanel's
    applyTransform on the frontend.
    """

    rotation_degrees: float
    center_x: float
    center_y: float
    scale: float
    viewbox_center: float


class CircuitCornerMarker(BaseModel):
    """One turn marker — circuit_info.corners' X/Y, already transformed into
    the outline's viewBox frame (same pipeline as `points`, see
    extract_circuit_outlines.py's _mirror_and_rotate).
    """

    number: int
    x: float
    y: float


class CircuitOutlineResponse(BaseModel):
    """GET /circuits/{circuit_id}/outline — Circuit.map_geometry, reshaped.

    See scripts/extract_circuit_outlines.py for how this is populated: a
    rotated/normalized track polyline in an SVG viewBox, extracted one-time
    from a real FastF1 session's fastest-lap position telemetry.
    """

    model_config = ConfigDict(from_attributes=True)

    circuit_id: uuid.UUID
    viewbox: str
    points: list[list[float]]
    corners: list[CircuitCornerMarker] = []
    source: dict[str, Any] | None = None
    transform: CircuitOutlineTransform | None = None
