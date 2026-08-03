// Mirrors backend/schemas/telemetry_schema.py

export interface SectorTimeResponse {
  id: string
  lap_data_id: string
  sector: number
  time_seconds: number
  mini_sector_speeds: unknown
}

export interface LapDataResponse {
  id: string
  session_id: string
  driver_id: string
  lap_number: number
  lap_time_seconds: number | null
  compound: string
  tyre_age_laps: number
  is_valid: boolean
  sector1_seconds: number | null
  sector2_seconds: number | null
  sector3_seconds: number | null
  created_at: string
  sector_times: SectorTimeResponse[]
}

export interface LapDataCreate {
  session_id: string
  driver_id: string
  lap_number: number
  lap_time_seconds?: number | null
  compound: string
  tyre_age_laps?: number
  is_valid?: boolean
  sector1_seconds?: number | null
  sector2_seconds?: number | null
  sector3_seconds?: number | null
}

export interface TireStintResponse {
  id: string
  session_id: string
  driver_id: string
  stint_number: number
  compound: string
  start_lap: number
  end_lap: number | null
  avg_deg_per_lap: number | null
}

// Single 100ms telemetry sample. Still unwired — see CLAUDE.md's "Deferred
// Telemetry Features": the WS endpoint broadcasts LapCompletedEvent instead.
export interface LiveTelemetryEvent {
  driver_id: string
  session_id: string
  timestamp_ms: number
  speed_kmh: number
  throttle_pct: number
  brake: boolean
  gear: number
  drs: boolean
}

export type DrsStatus = "off" | "available" | "enabled" | "open" | "unknown"

// WebSocket payload broadcast on /ws/telemetry/{session_id} when a new lap is
// ingested. speed_kmh/throttle_pct/brake/gear/drs are best-effort: read from
// the live CarData cache at broadcast time and null if that key has expired
// or no live ingestor is running for this session.
export interface LapCompletedEvent {
  driver_id: string
  session_id: string
  lap_number: number
  lap_time_seconds: number | null
  compound: string
  sector1_seconds: number | null
  sector2_seconds: number | null
  sector3_seconds: number | null
  speed_kmh: number | null
  throttle_pct: number | null
  brake: boolean | null
  gear: number | null
  drs: DrsStatus | null
}

// Envelope wrapping a LapCompletedEvent on the WebSocket stream.
export interface TelemetryStreamMessage {
  event: string
  session_id: string
  data: LapCompletedEvent
}

// GET /telemetry/{session_id}/{driver_id}/live — raw normalized CarData sample.
export interface LiveTelemetryResponse {
  session_id: string
  driver_id: string
  data: Record<string, unknown>
}

export interface LapHistoryBucket {
  bucket: string
  avg_sector1_seconds: number | null
  avg_sector2_seconds: number | null
  avg_sector3_seconds: number | null
  avg_lap_time_seconds: number | null
  lap_count: number
}

export interface DriverGap {
  driver_id: string
  lap_number: number
  position: number
  gap_to_ahead_seconds: number
  gap_to_behind_seconds: number
}

export interface SessionGapsResponse {
  session_id: string
  gaps: DriverGap[]
}
