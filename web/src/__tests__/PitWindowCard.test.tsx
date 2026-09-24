import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { usePitRecommendation, type PitRecommendationView } from "@/hooks/useStrategy"

// Checkpoint 5 (core-feature-rebuild): PitWindowCard now has ONE render path
// for both compact and full modes, driven entirely by usePitRecommendation's
// normalized PitRecommendationView — no isReplayActive branch in this
// component at all. usePitRecommendation's own source-selection/
// normalization logic is tested separately (usePitRecommendation.test.tsx);
// this file only exercises rendering given a view.
vi.mock("@/hooks/useStrategy", () => ({
  usePitRecommendation: vi.fn(),
}))

// Compact mode renders DriverChip, which resolves the driver roster via
// useDrivers()'s own useQuery call — mocked directly rather than wrapped in
// a real QueryClientProvider, same convention as LiveTimingTower.test.tsx.
vi.mock("@/hooks/useDrivers", () => ({
  useDrivers: vi.fn(() => ({ data: [] })),
}))

function buildView(overrides: Partial<PitRecommendationView> = {}): PitRecommendationView {
  const defaults: PitRecommendationView = {
    pitLap: 24,
    windowStart: 22,
    windowEnd: 26,
    recommendedCompound: "MEDIUM",
    confidenceScore: 0.71,
    explanation: null,
    pitProbability: null,
    asOfLapNumber: null,
    isFallbackEstimate: false,
  }
  return { ...defaults, ...overrides }
}

function mockView(view: PitRecommendationView | null, isLoading = false) {
  vi.mocked(usePitRecommendation).mockReturnValue({ view, isLoading })
}

describe("PitWindowCard", () => {
  it("renders the pit window lap range, recommended compound, and confidence", () => {
    mockView(buildView())

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("Lap 22–26")).toBeInTheDocument()
    expect(screen.getByText("Recommended: Lap 24 — MEDIUM")).toBeInTheDocument()
    expect(screen.getByText("71% confidence")).toBeInTheDocument()
  })

  it("shows the server-built plain-English narrative when present", () => {
    mockView(
      buildView({
        explanation: {
          facts: [],
          narrative: "Pit now — tyres are done and the gap behind is safe.",
          tire_deg_shap: [],
          pit_predictor_shap: [],
        },
      }),
    )

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(
      screen.getByText("Pit now — tyres are done and the gap behind is safe."),
    ).toBeInTheDocument()
  })

  it("shows pit_predictor's own signal honestly labeled as a lagging indicator", () => {
    mockView(buildView({ pitProbability: 0.9999 }))

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText(/Pit predictor: 100% \(lagging indicator/)).toBeInTheDocument()
  })

  it("omits the pit_predictor caption when pitProbability is unavailable", () => {
    mockView(buildView({ pitProbability: null }))

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.queryByText(/Pit predictor:/)).not.toBeInTheDocument()
  })

  it("falls back to a Lap {pitLap} headline when no window band is available", () => {
    mockView(buildView({ windowStart: null, windowEnd: null }))

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("Lap 24")).toBeInTheDocument()
  })

  it("flags a fallback estimate with a ~ headline, 'Estimated' label, and an unconfirmed-estimate note", () => {
    // docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue A: this
    // is the honest-uncertainty surface for a value that could still be an
    // unbounded, low-confidence pit_predictor estimate (a pre-fix persisted
    // row, or a live session before its first LapCount message) — the
    // frontend has no race-length context to sanity-check the number
    // itself, so it flags the uncertainty instead.
    mockView(
      buildView({
        windowStart: null,
        windowEnd: null,
        pitLap: 78,
        isFallbackEstimate: true,
      }),
    )

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("~Lap 78")).toBeInTheDocument()
    expect(screen.getByText("Estimated: Lap 78 — MEDIUM")).toBeInTheDocument()
    expect(
      screen.getByText("Unconfirmed estimate — not yet bounded by a real pit-window search."),
    ).toBeInTheDocument()
  })

  it("does not flag a real bounded recommendation as a fallback estimate", () => {
    mockView(buildView()) // isFallbackEstimate: false by default

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("Recommended: Lap 24 — MEDIUM")).toBeInTheDocument()
    expect(
      screen.queryByText("Unconfirmed estimate — not yet bounded by a real pit-window search."),
    ).not.toBeInTheDocument()
  })

  it("shows the no-window empty state when there is no view at all", () => {
    mockView(null)

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("No pit window predicted.")).toBeInTheDocument()
  })

  it("compact mode renders a terse L-prefixed headline and confidence/as-of-lap caption", () => {
    mockView(buildView({ asOfLapNumber: 20 }))

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" compact />)

    expect(screen.getByText("L22–26")).toBeInTheDocument()
    expect(screen.getByText("71% confidence — as of lap 20")).toBeInTheDocument()
  })

  it("compact mode shows 'No prediction yet' when there is no view", () => {
    mockView(null)

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" compact />)

    expect(screen.getByText("No prediction yet")).toBeInTheDocument()
  })
})
