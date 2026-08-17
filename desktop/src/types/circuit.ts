// Mirrors backend/schemas/circuit_schema.py

// Rotation/center/scale extract_circuit_outlines.py applied to build
// `points` from a real FastF1 lap's raw X/Y. Apply this identical transform
// to raw (unrotated, unnormalized) live Position.z X/Y so driver dots land
// in the same viewBox frame as the outline polyline underneath them.
export interface CircuitOutlineTransform {
  rotation_degrees: number
  center_x: number
  center_y: number
  scale: number
  viewbox_center: number
}

// One turn marker — circuit_info.corners' X/Y, already transformed into the
// outline's viewBox frame server-side (same pipeline as `points`).
export interface CircuitCornerMarker {
  number: number
  x: number
  y: number
}

export interface CircuitOutlineResponse {
  circuit_id: string
  viewbox: string
  points: number[][]
  corners: CircuitCornerMarker[]
  source: Record<string, unknown> | null
  transform: CircuitOutlineTransform | null
}
