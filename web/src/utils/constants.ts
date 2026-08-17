import type { CSSProperties } from "react"

export const API_URL = import.meta.env.VITE_API_URL
export const WS_URL = import.meta.env.VITE_WS_URL

// Recharts' <Tooltip> renders with a hardcoded light-mode background by
// default — no dark-mode awareness at all. This app is permanently dark
// (see DESIGN.md's "no light-mode components" rule), so every chart
// tooltip needs this override. Spread onto every <Tooltip> instance:
// <Tooltip formatter={...} {...CHART_TOOLTIP_STYLE} />
export const CHART_TOOLTIP_STYLE: {
  contentStyle: CSSProperties
  labelStyle: CSSProperties
  itemStyle: CSSProperties
} = {
  contentStyle: {
    backgroundColor: "var(--card)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    color: "var(--foreground)",
    fontSize: "0.75rem",
  },
  labelStyle: {
    color: "var(--muted-foreground)",
    marginBottom: 4,
  },
  itemStyle: {
    color: "var(--foreground)",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontVariantNumeric: "tabular-nums",
  },
}

// Used wherever a driver's team/color_hex is unresolved (no contract, no
// team assignment). Single source of truth — was previously duplicated
// verbatim across 7 components.
export const FALLBACK_TEAM_COLOR = "#6B7280"

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
  LIVE_RACE: "/race",
  race: (sessionId: string) => `/race/${sessionId}`,
  raceStrategy: (sessionId: string) => `/race/${sessionId}/strategy`,
  raceLive: (sessionId: string) => `/race/${sessionId}/live`,
  driver: (driverId: string) => `/drivers/${driverId}`,
  SIMULATE: "/simulate",
  ALERTS: "/alerts",
  SETTINGS: "/settings",
} as const
