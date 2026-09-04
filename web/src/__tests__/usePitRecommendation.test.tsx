import { renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { usePitRecommendation } from "@/hooks/useStrategy"
import type { LapCompletedEvent, PitWindowResponse, StrategyPredictionHistoryEntry } from "@/types"

// usePitRecommendation (Checkpoint 5, core-feature-rebuild) is the
// unification this file tests directly: it calls two sibling hooks from the
// SAME module (useCurrentLapHistoryEntry, usePitWindow), which in turn call
// useLiveTelemetry and useQuery. vi.mock on @/hooks/useStrategy itself
// doesn't intercept those sibling calls (they're intra-module references,
// not re-imports through the mocked module boundary) — so this mocks one
// level lower instead: useLiveTelemetry (controls isReplayActive) and
// @tanstack/react-query's useQuery (controls each query's data), same
// importOriginal pattern already used elsewhere in this suite (see
// SectorHeatmap.test.tsx's useQueries mock). This exercises
// usePitRecommendation's REAL source-selection/normalization logic, not a
// hand-derived approximation of it.
let lapsByDriverFixture: Record<string, LapCompletedEvent> = {}

vi.mock("@/hooks/useLiveTelemetry", () => ({
  useLiveTelemetry: () => ({
    lapsByDriver: lapsByDriverFixture,
    readyState: "open",
    staleConnection: false,
  }),
}))

let historyResult: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false }
let pitWindowResult: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false }

// vi.mock factories are hoisted above regular const/let declarations, so the
// mock function itself must be created via vi.hoisted rather than referenced
// as a plain top-level const (which would be a temporal-dead-zone access at
// the point the hoisted factory runs).
const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}))

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return { ...actual, useQuery: useQueryMock }
})

function buildHistoryEntry(
  overrides: Partial<StrategyPredictionHistoryEntry> = {},
): StrategyPredictionHistoryEntry {
  return {
    lap_number: 24,
    predicted_pit_lap: 25,
    pit_probability: 0.9999,
    undercut_score: 0.3,
    overcut_score: 0.4,
    created_at: "2026-01-01T00:00:00Z",
    recommended_pit_lap: 26,
    window_start: 24,
    window_end: 28,
    recommended_compound: "MEDIUM",
    confidence_score: 0.71,
    explanation: {
      facts: [],
      narrative: "Test narrative",
      tire_deg_shap: [],
      pit_predictor_shap: [],
    },
    ...overrides,
  }
}

function buildPitWindow(overrides: Partial<PitWindowResponse> = {}): PitWindowResponse {
  return {
    pit_lap: 24,
    window_start: 22,
    window_end: 26,
    projected_total_delta_seconds: -3.2,
    recommended_compound: "SOFT",
    confidence_score: 0.8,
    explanation: null,
    ...overrides,
  }
}

function setReplaying(lapNumber: number | null) {
  lapsByDriverFixture =
    lapNumber === null
      ? {}
      : {
          "driver-1": {
            driver_id: "driver-1",
            session_id: "session-1",
            lap_number: lapNumber,
            lap_time_seconds: 90,
            compound: "MEDIUM",
            sector1_seconds: null,
            sector2_seconds: null,
            sector3_seconds: null,
            speed_kmh: null,
            throttle_pct: null,
            brake: null,
            gear: null,
            drs: null,
          },
        }
}

beforeEach(() => {
  useQueryMock.mockReset()
  useQueryMock.mockImplementation((options: { queryKey: readonly unknown[] }) => {
    const kind = options.queryKey[1]
    if (kind === "history") return historyResult
    if (kind === "pit-window") return pitWindowResult
    return { data: undefined, isLoading: false }
  })
  historyResult = { data: undefined, isLoading: false }
  pitWindowResult = { data: undefined, isLoading: false }
  setReplaying(null)
})

describe("usePitRecommendation", () => {
  it("uses the history source (normalized) while replay/live is active", () => {
    setReplaying(24)
    historyResult = { data: { predictions: [buildHistoryEntry()] }, isLoading: false }

    const { result } = renderHook(() => usePitRecommendation("session-1", "driver-1"))

    expect(result.current.view).toEqual({
      pitLap: 26,
      windowStart: 24,
      windowEnd: 28,
      recommendedCompound: "MEDIUM",
      confidenceScore: 0.71,
      explanation: expect.objectContaining({ narrative: "Test narrative" }) as unknown,
      pitProbability: 0.9999,
      asOfLapNumber: 24,
    })
  })

  it("falls back to predicted_pit_lap and null confidence/window when the row has no rich recommendation", () => {
    setReplaying(24)
    historyResult = {
      data: {
        predictions: [
          buildHistoryEntry({
            recommended_pit_lap: null,
            window_start: null,
            window_end: null,
            recommended_compound: null,
            confidence_score: 0.0,
            explanation: null,
          }),
        ],
      },
      isLoading: false,
    }

    const { result } = renderHook(() => usePitRecommendation("session-1", "driver-1"))

    expect(result.current.view?.pitLap).toBe(25) // predicted_pit_lap fallback
    expect(result.current.view?.confidenceScore).toBeNull()
    expect(result.current.view?.windowStart).toBeNull()
    expect(result.current.view?.pitProbability).toBe(0.9999)
  })

  it("returns a null view during replay when there is no eligible history entry yet", () => {
    setReplaying(3)
    // Only a lap-28 prediction exists so far — not yet eligible at lap 3.
    historyResult = {
      data: { predictions: [buildHistoryEntry({ lap_number: 28 })] },
      isLoading: false,
    }

    const { result } = renderHook(() => usePitRecommendation("session-1", "driver-1"))

    expect(result.current.view).toBeNull()
  })

  it("uses the REST /pit-window source (normalized) when not replaying", () => {
    pitWindowResult = { data: [buildPitWindow()], isLoading: false }

    const { result } = renderHook(() => usePitRecommendation("session-1", "driver-1"))

    expect(result.current.view).toEqual({
      pitLap: 24,
      windowStart: 22,
      windowEnd: 26,
      recommendedCompound: "SOFT",
      confidenceScore: 0.8,
      explanation: null,
      pitProbability: null,
      asOfLapNumber: null,
    })
  })

  it("returns a null view when the REST source has no candidates", () => {
    pitWindowResult = { data: [], isLoading: false }

    const { result } = renderHook(() => usePitRecommendation("session-1", "driver-1"))

    expect(result.current.view).toBeNull()
  })

  it("disables usePitWindow's own query while replay/live is active", () => {
    setReplaying(24)
    historyResult = { data: { predictions: [buildHistoryEntry()] }, isLoading: false }

    renderHook(() => usePitRecommendation("session-1", "driver-1"))

    const pitWindowCall = useQueryMock.mock.calls.find(
      ([options]) => options.queryKey[1] === "pit-window",
    )
    expect(pitWindowCall?.[0]).toMatchObject({ enabled: false })
  })
})
