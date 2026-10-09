import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"
import { useSharedLiveTelemetry } from "@/hooks/useSharedLiveTelemetry"
import type {
  PitRecommendationExplanation,
  PitWindowResponse,
  SimulateStrategyRequest,
  SimulateTaskStatusResponse,
  StrategyPredictionHistoryEntry,
} from "@/types"

// Hand-written — mirrors web/src/hooks/useStrategy.ts. usePitWindow and
// useStrategyOverview were ported first (Strategy tab's needs);
// useSimulateStrategy/useSimulationResult added Day 32 Checkpoint 4 for the
// Simulator screen; useUndercut 2026-10-07 for the Strategy tab's driver
// sheet. useCurrentLapHistoryEntry and usePitRecommendation in Day 6b-mobile
// CP4 (2026-10-09), so a replay shows stored predictions.

// enabled lets a caller switch the live ML call off, as on web: during a
// replay the stored prediction for the current lap is authoritative.
export function usePitWindow(sessionId: string | null, driverId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["strategy", "pit-window", sessionId, driverId],
    queryFn: () => strategyApi.getPitWindow(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId) && enabled,
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

export interface UseCurrentLapHistoryEntryResult {
  // The stored prediction valid at the driver's current replay/live lap; null
  // while not replaying/live, or before any prediction exists that early.
  entry: StrategyPredictionHistoryEntry | null
  // True once the live connection has delivered a lap event for this driver:
  // a live race or a Demo Replay is progressing, so the stored per-lap
  // predictions apply rather than the always-latest endpoints.
  isReplayActive: boolean
  isLoading: boolean
}

// Mirrors web's useCurrentLapHistoryEntry, reading lap events from the app's
// one shared connection (useSharedLiveTelemetry) instead of opening its own:
// the Strategy tab shows about 20 of these at once.
export function useCurrentLapHistoryEntry(
  sessionId: string | null,
  driverId: string | null,
): UseCurrentLapHistoryEntryResult {
  const { lapsByDriver } = useSharedLiveTelemetry(sessionId)
  const liveEvent = driverId ? lapsByDriver[driverId] : undefined
  const isReplayActive = liveEvent !== undefined

  const query = useQuery({
    // The lap in the key makes this follow the race: /history is uncached
    // and this has no polling of its own (as web).
    queryKey: ["strategy", "history", sessionId, driverId, liveEvent?.lap_number],
    queryFn: () => strategyApi.getStrategyHistory(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId && isReplayActive),
  })

  const entry = useMemo(() => {
    if (!isReplayActive || liveEvent === undefined || !query.data) return null
    const currentLap = liveEvent.lap_number
    // Rows with no lap_number (predicted before it existed) are never "valid
    // at this lap".
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

// What PitWindowCard renders, from either source (copied from web's
// useStrategy.ts, where the field-by-field reasoning is documented).
export interface PitRecommendationView {
  pitLap: number
  windowStart: number | null
  windowEnd: number | null
  recommendedCompound: string | null
  confidenceScore: number | null
  explanation: PitRecommendationExplanation | null
  // pit_predictor's own signal; only the stored (history) source has it.
  pitProbability: number | null
  // The lap the stored prediction is as of; null from the /pit-window fetch.
  asOfLapNumber: number | null
  // pitLap is pit_predictor's cruder estimate, not the recommendation
  // engine's (a degraded or old stored row).
  isFallbackEstimate: boolean
}

function viewFromPitWindow(window: PitWindowResponse | undefined): PitRecommendationView | null {
  if (!window) return null
  return {
    pitLap: window.pit_lap,
    windowStart: window.window_start,
    windowEnd: window.window_end,
    recommendedCompound: window.recommended_compound,
    confidenceScore: window.confidence_score,
    explanation: window.explanation,
    pitProbability: null,
    asOfLapNumber: null,
    isFallbackEstimate: false,
  }
}

function viewFromHistoryEntry(
  entry: StrategyPredictionHistoryEntry | null,
): PitRecommendationView | null {
  if (!entry) return null
  const hasRecommendation = entry.recommended_pit_lap !== null
  return {
    pitLap: entry.recommended_pit_lap ?? entry.predicted_pit_lap,
    windowStart: entry.window_start,
    windowEnd: entry.window_end,
    recommendedCompound: entry.recommended_compound,
    // The column defaults to 0.0 on a degraded row: unknown, not 0%.
    confidenceScore: hasRecommendation ? entry.confidence_score : null,
    explanation: entry.explanation,
    pitProbability: entry.pit_probability,
    asOfLapNumber: entry.lap_number,
    isFallbackEstimate: !hasRecommendation,
  }
}

export interface UsePitRecommendationResult {
  view: PitRecommendationView | null
  isLoading: boolean
}

// Mirrors web's usePitRecommendation: the stored prediction for the current
// lap while a live race or replay progresses (and no /pit-window ML call),
// else the on-demand /pit-window recompute.
export function usePitRecommendation(
  sessionId: string | null,
  driverId: string | null,
): UsePitRecommendationResult {
  const { entry: historyEntry, isReplayActive, isLoading: historyLoading } =
    useCurrentLapHistoryEntry(sessionId, driverId)
  const { data: windows, isLoading: windowLoading } = usePitWindow(sessionId, driverId, !isReplayActive)

  if (isReplayActive) {
    return { view: viewFromHistoryEntry(historyEntry), isLoading: historyLoading }
  }
  return { view: viewFromPitWindow(windows?.[0]), isLoading: windowLoading }
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
