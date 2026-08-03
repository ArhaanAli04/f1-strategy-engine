export const API_URL = import.meta.env.VITE_API_URL
export const WS_URL = import.meta.env.VITE_WS_URL

// Official FIA/F1 broadcast tire compound colors.
export const COMPOUND_COLORS: Record<string, string> = {
  SOFT: "#DA291C",
  MEDIUM: "#FFD12E",
  HARD: "#F0F0F0",
  INTERMEDIATE: "#43B02A",
  WET: "#0067AD",
  UNKNOWN: "#9CA3AF",
}

export const ROUTES = {
  LOGIN: "/login",
  REGISTER: "/register",
  DASHBOARD: "/dashboard",
  race: (sessionId: string) => `/race/${sessionId}`,
  raceStrategy: (sessionId: string) => `/race/${sessionId}/strategy`,
  raceLive: (sessionId: string) => `/race/${sessionId}/live`,
  driver: (driverId: string) => `/drivers/${driverId}`,
  SIMULATE: "/simulate",
  ALERTS: "/alerts",
  SETTINGS: "/settings",
} as const
