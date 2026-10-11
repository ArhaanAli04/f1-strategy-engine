// Adapted from web/src/utils/constants.ts. Expo inlines any env var prefixed
// EXPO_PUBLIC_ into the bundle at build time (no extra package needed, SDK
// 49+) — set EXPO_PUBLIC_API_URL/EXPO_PUBLIC_WS_URL in mobile/.env to your
// dev machine's LAN IP (e.g. http://192.168.1.20:8000) so a physical device
// on the same WiFi can reach the backend; localhost would resolve to the
// device itself, not the dev machine.
export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? ""
export const WS_URL = process.env.EXPO_PUBLIC_WS_URL ?? ""

// CHART_TOOLTIP_STYLE is dropped here — it styled Recharts' web-only
// <Tooltip>, and no chart library is wired up yet (victory-native is
// deferred to Day 32, see CLAUDE.md's Deferred Wiring).

// Used wherever a driver's team/color_hex is unresolved (no contract, no
// team assignment). Mirrors web's FALLBACK_TEAM_COLOR.
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

// Expo Router file-based paths (mirrors app/ directory structure) — not
// web's react-router path strings. See web/src/utils/constants.ts's ROUTES
// for the web equivalent.
export const ROUTES = {
  LOGIN: "/(auth)/login",
  REGISTER: "/(auth)/register",
  HOME: "/(tabs)/",
  LIVE: "/(tabs)/live",
  STRATEGY: "/(tabs)/strategy",
  DRIVERS: "/(tabs)/drivers",
  ALERTS: "/(tabs)/alerts",
  SETTINGS: "/settings",
  SIMULATOR: "/simulator",
  STRATEGY_DRIVER: "/strategy-driver",
  // Explicit template-literal return type — Expo Router's typed routes
  // (experiments.typedRoutes in app.json) reject a plain `string` return
  // here even once `app/driver/[id].tsx` is a known route.
  DRIVER_DETAIL: (driverId: string): `/driver/${string}` => `/driver/${driverId}`,
} as const
