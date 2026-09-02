// Mirrors backend/schemas/demo_schema.py

export interface CuratedSession {
  session_id: string
  race_name: string
  circuit_name: string
  description: string
  start_lap: number
  end_lap: number
  estimated_duration_minutes: number
}

export interface CuratedSessionsResponse {
  sessions: CuratedSession[]
}

export interface ReplayAvailableResponse {
  available: boolean
  // Populated only when available is false — the live-race reason.
  reason: string | null
}

export interface ReplayStartResponse {
  replay_id: string
  session_id: string
  race_name: string
  start_lap: number
  end_lap: number
}

export interface ReplayStatusResponse {
  running: boolean
  replay_id: string | null
  session_id: string | null
  race_name: string | null
  start_lap: number | null
  end_lap: number | null
  started_at: string | null
}

export interface ReplayStopResponse {
  stopped: boolean
  session_id: string
}
