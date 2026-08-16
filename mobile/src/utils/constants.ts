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
  // Explicit template-literal return type — Expo Router's typed routes
  // (experiments.typedRoutes in app.json) reject a plain `string` return
  // here even once `app/driver/[id].tsx` is a known route.
  DRIVER_DETAIL: (driverId: string): `/driver/${string}` => `/driver/${driverId}`,
} as const
