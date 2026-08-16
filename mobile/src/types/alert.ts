// Mirrors backend/schemas/alert_schema.py

// erasableSyntaxOnly forbids real `enum` — this is the erasable equivalent.
export const AlertType = {
  UNDERCUT_THREAT: "UNDERCUT_THREAT",
  PIT_WINDOW_OPEN: "PIT_WINDOW_OPEN",
  SAFETY_CAR_PROBABILITY: "SAFETY_CAR_PROBABILITY",
  FASTEST_LAP_THREAT: "FASTEST_LAP_THREAT",
  COMPETITOR_PITTED: "COMPETITOR_PITTED",
} as const

export type AlertType = (typeof AlertType)[keyof typeof AlertType]

export interface AlertCreate {
  user_id: string
  session_id: string
  alert_type: AlertType
  driver_id?: string | null
  message: string
  triggered_at: string
}

export interface AlertResponse {
  id: string
  user_id: string
  session_id: string
  // Backend types this as plain str, not AlertType — mirrored as-is.
  alert_type: string
  driver_id: string | null
  message: string
  triggered_at: string
  delivered_at: string | null
  read_at: string | null
}
