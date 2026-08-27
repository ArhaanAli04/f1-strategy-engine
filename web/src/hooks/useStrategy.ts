import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, type Query } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import type {
  SimulateStrategyRequest,
  SimulateTaskStatusResponse,
  StrategyPredictionHistoryEntry,
} from "@/types"

// Worker is scaled to 0 outside race weekends (Day 40 hybrid deployment,
// see fly.toml) — nothing ever consumes prediction_queue, so a task
// enqueued then would poll PENDING/STARTED forever with no signal. After
// this long in that state, useSimulationResult flags timedOut so the UI
// can swap the spinner for an explanation instead of hanging silently.
const PENDING_TIMEOUT_MS = 60_000

// `enabled` defaults true; callers pass false to skip the live ML-inference
// recompute — e.g. PitWindowCard/UndercutThreatPanel during Demo Replay,
// where useCurrentLapHistoryEntry's lap-gated history read is authoritative
// and this endpoint's always-current answer would be both wrong and a
// wasted expensive call (see CLAUDE.md's /overview cold-compute-floor notes
// — /pit-window and /undercut share the same per-call ML cost).
export function usePitWindow(sessionId: string | null, driverId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["strategy", "pit-window", sessionId, driverId],
    queryFn: () => strategyApi.getPitWindow(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId) && enabled,
  })
}

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

export interface UseCurrentLapHistoryEntryResult {
  // The prediction that was valid AT the driver's current replay/live lap —
  // null while not replaying/live, or before any prediction exists for a
  // lap this early.
  entry: StrategyPredictionHistoryEntry | null
  // True whenever useLiveTelemetry has ever delivered a WS lap-completion
  // event for this driver this page session — same signal LapTimeChart/
  // SectorHeatmap use (see their Day 42 fix) to distinguish "a live or
  // replay session is progressing" from "plain historical viewing of an
  // already-completed race", where the live/overview endpoints' end-of-race
  // state is already correct and this hook has nothing useful to add.
  isReplayActive: boolean
  isLoading: boolean
}

// Strategy Wall (PitWindowCard, compact) and UndercutThreatPanel both need
// "the prediction as it stood at the driver's current lap" instead of
// /overview's and /undercut's always-latest state — this is the shared
// lap-gating logic both consume, built on Day 42's history endpoint
// (GET /strategy/{session}/{driver}/history) the same way LapTimeChart/
// SectorHeatmap gate their own REST-fetched datasets on useLiveTelemetry's
// current-lap signal.
export function useCurrentLapHistoryEntry(
  sessionId: string | null,
  driverId: string | null,
): UseCurrentLapHistoryEntryResult {
  const { lapsByDriver } = useLiveTelemetry(sessionId)
  const liveEvent = driverId ? lapsByDriver[driverId] : undefined
  const isReplayActive = liveEvent !== undefined

  const query = useQuery({
    // liveEvent.lap_number in the key (not just enabled) is what makes this
    // actually track progression — /history is deliberately uncached
    // (see backend docstring) and this hook has no polling interval of its
    // own, so without a key that changes every lap, react-query would fetch
    // once when isReplayActive first flips true and then never again, and
    // "as of lap N" would freeze at whatever lap that was.
    queryKey: ["strategy", "history", sessionId, driverId, liveEvent?.lap_number],
    queryFn: () => strategyApi.getStrategyHistory(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId && isReplayActive),
  })

  const entry = useMemo(() => {
    if (!isReplayActive || liveEvent === undefined || !query.data) return null
    const currentLap = liveEvent.lap_number
    // Rows predicted before the Day 42 lap_number migration have no
    // lap_number and can never be "valid at this lap" — excluded, not
    // treated as always-eligible.
    const eligible = query.data.predictions.filter(
      (prediction): prediction is StrategyPredictionHistoryEntry & { lap_number: number } =>
        prediction.lap_number !== null && prediction.lap_number <= currentLap,
    )
    if (eligible.length === 0) return null
    return eligible.reduce((latest, prediction) =>
      prediction.lap_number > latest.lap_number ? prediction : latest,
    )
  }, [query.data, isReplayActive, liveEvent])

  return { entry, isReplayActive, isLoading: query.isLoading }
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
