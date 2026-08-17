// Mirrors backend/schemas/driver_schema.py

export interface TeamResponse {
  id: string
  name: string
  constructor_id: string
  color_hex: string
}

export interface DriverContractResponse {
  id: string
  driver_id: string
  team_id: string
  season: number
  team: TeamResponse | null
}

export interface DriverResponse {
  id: string
  code: string
  full_name: string
  nationality: string
  date_of_birth: string | null
  contracts: DriverContractResponse[]
}

export interface DriverListResponse {
  id: string
  code: string
  full_name: string
  nationality: string
  date_of_birth: string | null
}

// Driver-style fingerprint (see services/ml/driver_style.py) plus session-relative
// performance. Not ORM-backed — assembled from a cached population-level cluster
// fit and a live lap-time aggregate query.
export interface DriverAnalysisResponse {
  driver_id: string
  season: number
  archetype: string
  cluster: number
  sector_time_variance: number
  tyre_management_index: number
  lap_time_consistency: number
  stint_length_tendency: number
  umap_x: number
  umap_y: number
  performance_vs_team_avg_seconds: number | null
}
