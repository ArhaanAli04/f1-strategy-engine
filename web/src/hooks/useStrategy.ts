import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, type Query } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import type {
  PitRecommendationExplanation,
  PitWindowResponse,
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

// --- usePitRecommendation: the Checkpoint 5 unification ---
//
// PitWindowCard.tsx used to branch its own JSX on isReplayActive — a
// separate, reduced-detail render path during replay/live progression (built
// from useCurrentLapHistoryEntry's history) vs. the full render path
// otherwise (built from usePitWindow's REST fetch). That branch existed
// because the two sources used to carry genuinely different data:
// StrategyPredictionHistoryEntry had no window/compound/confidence/
// explanation at all before the core-feature-rebuild's Checkpoint 4 wired
// the SAME recommendation engine into the per-lap persisted row. Now both
// sources carry the same rich shape (just under different field names —
// PitWindowResponse.pit_lap vs. StrategyPredictionHistoryEntry.
// recommended_pit_lap, etc.), so this hook normalizes both into ONE view
// model and picks a source; the component below renders from that view
// alone and no longer needs to know which source it came from.

// Normalized shape PitWindowCard actually renders — deliberately NOT a
// re-export of either backend response type, so the component has one
// consistent set of field names regardless of source.
export interface PitRecommendationView {
  // Falls back to StrategyPredictionHistoryEntry.predicted_pit_lap (pit_
  // predictor's own, cruder estimate) when the rich recommendation isn't
  // available for this row (a pre-Checkpoint-4 row, or a lap where the
  // computation degraded gracefully) — see fromHistoryEntry below. Never
  // null when a source resolved at all.
  pitLap: number
  windowStart: number | null
  windowEnd: number | null
  recommendedCompound: string | null
  confidenceScore: number | null
  explanation: PitRecommendationExplanation | null
  // pit_predictor's own lagging-indicator signal (CLAUDE.md: fires ~on the
  // pit lap itself, not before) — surfaced honestly as a labeled secondary
  // signal, never as the headline. Only ever available from the history
  // source; PitWindowResponse carries no pit_predictor read of its own.
  pitProbability: number | null
  // The lap this view was valid as of, when sourced from history (live/
  // replay progression) — null when sourced from the always-current
  // /pit-window REST fetch, which has no single "as of" lap.
  asOfLapNumber: number | null
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
    // Only meaningful alongside a real recommendation — the column
    // defaults to 0.0 (not null) on a degraded/pre-migration row, which
    // would otherwise misrender as "0% confidence" rather than "unknown".
    confidenceScore: hasRecommendation ? entry.confidence_score : null,
    explanation: entry.explanation,
    pitProbability: entry.pit_probability,
    asOfLapNumber: entry.lap_number,
  }
}

export interface UsePitRecommendationResult {
  view: PitRecommendationView | null
  isLoading: boolean
}

// Single hook backing PitWindowCard in both its compact (Strategy Wall) and
// full (RacePage right rail) modes — the component itself never checks
// isReplayActive; it renders PitRecommendationView fields only. Source
// selection (history vs. REST) still legitimately differs by scenario (a
// lap-gated DB read during live/replay progression vs. an on-demand
// recompute otherwise are fundamentally different plumbing, not a
// stylistic choice), but that selection is made HERE, once, not
// re-decided in the component's JSX.
export function usePitRecommendation(
  sessionId: string | null,
  driverId: string | null,
): UsePitRecommendationResult {
  const { entry: historyEntry, isReplayActive, isLoading: historyLoading } =
    useCurrentLapHistoryEntry(sessionId, driverId)
  // Skip the live ML-inference recompute while a replay/live session is
  // progressing for this driver — the history source above is authoritative
  // and current then; see usePitWindow's own docstring.
  const { data: windows, isLoading: windowLoading } = usePitWindow(
    sessionId,
    driverId,
    !isReplayActive,
  )

  if (isReplayActive) {
    return { view: viewFromHistoryEntry(historyEntry), isLoading: historyLoading }
  }
  return { view: viewFromPitWindow(windows?.[0]), isLoading: windowLoading }
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
