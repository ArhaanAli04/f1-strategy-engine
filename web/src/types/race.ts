// Mirrors backend/schemas/race_schema.py

export interface CircuitResponse {
  id: string
  name: string
  country: string
  track_length_km: number
  lap_record_seconds: number | null
  created_at: string
}

export interface SessionResponse {
  id: string
  race_id: string
  session_type: string
  session_date: string
}

export interface RaceResponse {
  id: string
  season: number
  round_number: number
  circuit_id: string
  race_date: string
  weather: string | null
  status: string
  circuit: CircuitResponse | null
  sessions: SessionResponse[]
}

export interface RaceListResponse {
  id: string
  season: number
  round_number: number
  circuit_id: string
  race_date: string
  weather: string | null
  status: string
  circuit: CircuitResponse | null
}

// GET /races/upcoming — minimal shape for the Circuit Map Panel's
// NON-RACE-mode countdown: display name, circuit, and the Race session's
// real start instant.
export interface UpcomingRaceResponse {
  id: string
  season: number
  round_number: number
  race_name: string | null
  circuit_id: string
  race_date: string
  scheduled_start: string | null
}
