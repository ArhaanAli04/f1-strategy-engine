import { useMutation, useQuery, type Query } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"
import type { SimulateStrategyRequest, SimulateTaskStatusResponse } from "@/types"

// Hand-written — mirrors web/src/hooks/useStrategy.ts. usePitWindow and
// useStrategyOverview were ported first (Strategy tab's needs); useUndercut
// still isn't ported (no mobile consumer yet) — useSimulateStrategy/
// useSimulationResult added Day 32 Checkpoint 4 for the Simulator screen.
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

export function useSimulateStrategy(sessionId: string) {
  return useMutation({
    mutationFn: (payload: SimulateStrategyRequest) =>
      strategyApi.simulateStrategy(sessionId, payload),
  })
}

// Polls GET /strategy/simulate/{task_id} until the Celery task resolves.
export function useSimulationResult(taskId: string | null) {
  return useQuery({
    queryKey: ["strategy", "simulate-result", taskId],
    queryFn: () => strategyApi.getSimulationResult(taskId as string),
    enabled: Boolean(taskId),
    refetchInterval: (query: Query<SimulateTaskStatusResponse>) => {
      const status = query.state.data?.status
      return status === "SUCCESS" || status === "FAILURE" ? false : 2000
    },
  })
}
