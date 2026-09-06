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

// Categorical identity color for the Strategy Simulator's Compare Scenarios
// mode — one color per candidate scenario (max 4, matches the backend's
// SimulateStrategyRequest.scenarios cap), used consistently between the
// scenario-builder rows and the finishing-position distribution chart so a
// scenario's color means the same thing everywhere it appears. This is
// identity color (which scenario), not semantic (good/bad) — unlike
// PlanExplanationCard's green/red gain/loss convention elsewhere on this
// page. Values are the dataviz skill's validated dark-mode categorical
// palette, slots 1-4 (blue/orange/aqua/yellow) — validated via
// validate_palette.js against this app's dark --card surface (#18181b,
// effectively identical to the palette's own #1a1a19 reference surface):
// worst adjacent CVD ΔE 8.4 (clears the >=8 target), normal-vision ΔE 19.8,
// all four >=3:1 contrast. A grouped bar chart uses the "adjacent" pairlist
// (not all-pairs), so all 4 slots are valid together, not just the first 3.
export const SCENARIO_SERIES_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500"]

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
