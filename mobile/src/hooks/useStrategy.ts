import { useQuery } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"

// Hand-written — mirrors web/src/hooks/useStrategy.ts. Only usePitWindow and
// useStrategyOverview are ported today (Strategy tab's needs) — useUndercut/
// useSimulateStrategy/useSimulationResult belong to the Simulator, out of
// scope for mobile so far.
export function usePitWindow(sessionId: string | null, driverId: string | null) {
  return useQuery({
    queryKey: ["strategy", "pit-window", sessionId, driverId],
    queryFn: () => strategyApi.getPitWindow(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId),
  })
}

export function useStrategyOverview(sessionId: string | null) {
  return useQuery({
    queryKey: ["strategy", "overview", sessionId],
    queryFn: () => strategyApi.getStrategyOverview(sessionId as string),
    enabled: Boolean(sessionId),
    // Cold path can take 16-17s (see CLAUDE.md's compute-floor notes) —
    // avoid refetch storms on remount/refocus within this window.
    staleTime: 30_000,
  })
}
