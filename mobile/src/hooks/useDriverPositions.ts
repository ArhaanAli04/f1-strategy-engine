import { useQuery } from "@tanstack/react-query"
import * as telemetryApi from "@/api/telemetry"

// Hand-written — mirrors web/src/hooks/useDriverPositions.ts exactly.
const POSITIONS_POLL_INTERVAL_MS = 2_000
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
// so dots can be colored by team and matched against the selected driver.
export function useDriverCarNumbers(sessionId: string | null) {
  return useQuery({
    queryKey: ["telemetry", "car-numbers", sessionId],
    queryFn: () => telemetryApi.getDriverCarNumbers(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: CAR_NUMBERS_POLL_INTERVAL_MS,
  })
}
