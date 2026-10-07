import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"
import type { SimulateStrategyRequest, SimulateTaskStatusResponse } from "@/types"

// Hand-written — mirrors web/src/hooks/useStrategy.ts. usePitWindow and
// useStrategyOverview were ported first (Strategy tab's needs);
// useSimulateStrategy/useSimulationResult added Day 32 Checkpoint 4 for the
// Simulator screen; useUndercut 2026-10-07 for the Strategy tab's driver
// sheet.
export function usePitWindow(sessionId: string | null, driverId: string | null) {
  return useQuery({
    queryKey: ["strategy", "pit-window", sessionId, driverId],
    queryFn: () => strategyApi.getPitWindow(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId),
  })
}

// Same as web's: "does driverId pitting now gain a position over target".
// enabled lets a caller switch the live ML call off (web does during a
// replay).
export function useUndercut(
  sessionId: string | null,
  driverId: string | null,
  target: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: ["strategy", "undercut", sessionId, driverId, target],
    queryFn: () =>
      strategyApi.getUndercut(sessionId as string, driverId as string, target as string),
    enabled: Boolean(sessionId && driverId && target) && enabled,
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

const SIMULATION_QUOTA_KEY = ["strategy", "simulation-quota"] as const

export function useSimulationQuota() {
  return useQuery({
    queryKey: SIMULATION_QUOTA_KEY,
    queryFn: strategyApi.getSimulationQuota,
  })
}

export function useSimulateStrategy(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: SimulateStrategyRequest) =>
      strategyApi.simulateStrategy(sessionId, payload),
    // On a refusal too: the daily limit is shared across the demo, so other
    // visitors may have used scenarios since the count was last fetched.
    onSettled: () => queryClient.invalidateQueries({ queryKey: SIMULATION_QUOTA_KEY }),
  })
}

// In production the worker machine is stopped until a simulation is queued,
// then started on demand (demo deployment Day 6): a cold start takes about a
// minute before the simulation itself runs. Past SLOW_START_MS
// useSimulationResult flags slowStart so the screen can say so; past
// PENDING_TIMEOUT_MS it flags timedOut, and the screen stops waiting. Same
// values and logic as web/src/hooks/useStrategy.ts.
const SLOW_START_MS = 10_000
const PENDING_TIMEOUT_MS = 180_000

// Polls GET /strategy/simulate/{task_id} until the Celery task resolves.
export function useSimulationResult(taskId: string | null) {
  const query = useQuery({
    queryKey: ["strategy", "simulate-result", taskId],
    queryFn: () => strategyApi.getSimulationResult(taskId as string),
    enabled: Boolean(taskId),
    refetchInterval: (query: Query<SimulateTaskStatusResponse>) => {
      const status = query.state.data?.status
      return status === "SUCCESS" || status === "FAILURE" ? false : 2000
    },
  })

  const [slowStart, setSlowStart] = useState(false)
  const [timedOut, setTimedOut] = useState(false)
  const pendingSinceRef = useRef<number | null>(null)
  const status = query.data?.status

  useEffect(() => {
    if (!taskId || status === "SUCCESS" || status === "FAILURE") {
      pendingSinceRef.current = null
      setSlowStart(false)
      setTimedOut(false)
      return
    }
    pendingSinceRef.current ??= Date.now()
    const elapsed = Date.now() - pendingSinceRef.current
    setSlowStart(elapsed >= SLOW_START_MS)
    setTimedOut(elapsed >= PENDING_TIMEOUT_MS)
    const timers = [
      setTimeout(() => setSlowStart(true), Math.max(0, SLOW_START_MS - elapsed)),
      setTimeout(() => setTimedOut(true), Math.max(0, PENDING_TIMEOUT_MS - elapsed)),
    ]
    return () => timers.forEach((timer) => clearTimeout(timer))
  }, [taskId, status])

  return { ...query, slowStart, timedOut }
}
