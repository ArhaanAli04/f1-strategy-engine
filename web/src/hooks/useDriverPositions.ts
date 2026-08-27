import { useQuery } from "@tanstack/react-query"
import * as telemetryApi from "@/api/telemetry"

// Matches the backend's real ~1Hz position publish cadence (both the live
// Position.z-authenticated path and Demo Replay's position timeline — see
// CLAUDE.md's Day 43 notes) — polling any slower than the data actually
// changes means each glide step covers more than one publish interval's
// worth of movement. AnimatedDriverDots.tsx interpolates against this same
// interval (its own INTERPOLATION_WINDOW_MS constant, not imported from
// here — see that file and DESIGN.md's Motion section for why a CSS
// transition duration couldn't be tuned to fully hide the real interval).
const POSITIONS_POLL_INTERVAL_MS = 1_000

// Car-number->driver mapping changes far less often than position itself
// (only on a reserve-driver substitution) — a slower interval is enough.
const CAR_NUMBERS_POLL_INTERVAL_MS = 15_000

// GET /telemetry/{session_id}/positions returns [] (not an error) whenever
// the session isn't currently live — CircuitMapPanel reads that directly as
// its LIVE-vs-not signal, so this hook never needs a "not live" special case.
export function useDriverPositions(sessionId: string | null) {
  return useQuery({
    queryKey: ["telemetry", "positions", sessionId],
    queryFn: () => telemetryApi.getDriverPositions(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: POSITIONS_POLL_INTERVAL_MS,
  })
}

// Resolves DriverPosition.driver_number (a car number) back to a driver_id,
// so dots can be colored by team and matched against the selected driver —
// see backend/schemas/telemetry_schema.py's DriverCarNumber docstring.
export function useDriverCarNumbers(sessionId: string | null) {
  return useQuery({
    queryKey: ["telemetry", "car-numbers", sessionId],
    queryFn: () => telemetryApi.getDriverCarNumbers(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: CAR_NUMBERS_POLL_INTERVAL_MS,
  })
}
