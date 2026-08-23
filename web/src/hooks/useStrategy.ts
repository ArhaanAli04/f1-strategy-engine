import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, type Query } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"
import type { SimulateStrategyRequest, SimulateTaskStatusResponse } from "@/types"

// Worker is scaled to 0 outside race weekends (Day 40 hybrid deployment,
// see fly.toml) — nothing ever consumes prediction_queue, so a task
// enqueued then would poll PENDING/STARTED forever with no signal. After
// this long in that state, useSimulationResult flags timedOut so the UI
// can swap the spinner for an explanation instead of hanging silently.
const PENDING_TIMEOUT_MS = 60_000

export function usePitWindow(sessionId: string | null, driverId: string | null) {
  return useQuery({
    queryKey: ["strategy", "pit-window", sessionId, driverId],
    queryFn: () => strategyApi.getPitWindow(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId),
  })
}

export function useUndercut(
  sessionId: string | null,
  driverId: string | null,
  target: string | null,
) {
  return useQuery({
    queryKey: ["strategy", "undercut", sessionId, driverId, target],
    queryFn: () =>
      strategyApi.getUndercut(sessionId as string, driverId as string, target as string),
    enabled: Boolean(sessionId && driverId && target),
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
  const query = useQuery({
    queryKey: ["strategy", "simulate-result", taskId],
    queryFn: () => strategyApi.getSimulationResult(taskId as string),
    enabled: Boolean(taskId),
    refetchInterval: (query: Query<SimulateTaskStatusResponse>) => {
      const status = query.state.data?.status
      return status === "SUCCESS" || status === "FAILURE" ? false : 2000
    },
  })

  const [timedOut, setTimedOut] = useState(false)
  const pendingSinceRef = useRef<number | null>(null)
  const status = query.data?.status

  useEffect(() => {
    if (!taskId || status === "SUCCESS" || status === "FAILURE") {
      pendingSinceRef.current = null
      setTimedOut(false)
      return
    }
    pendingSinceRef.current ??= Date.now()
    const elapsed = Date.now() - pendingSinceRef.current
    if (elapsed >= PENDING_TIMEOUT_MS) {
      setTimedOut(true)
      return
    }
    const timer = window.setTimeout(() => setTimedOut(true), PENDING_TIMEOUT_MS - elapsed)
    return () => window.clearTimeout(timer)
  }, [taskId, status])

  return { ...query, timedOut }
}
