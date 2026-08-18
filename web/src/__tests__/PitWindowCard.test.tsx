import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { usePitWindow } from "@/hooks/useStrategy"
import type { PitWindowResponse } from "@/types"

vi.mock("@/hooks/useStrategy", () => ({ usePitWindow: vi.fn() }))

function buildWindow(overrides: Partial<PitWindowResponse> = {}): PitWindowResponse {
  return {
    pit_lap: 24,
    window_start: 22,
    window_end: 26,
    projected_total_delta_seconds: -3.2,
    shap_explanation: null,
    ...overrides,
  }
}

function mockWindows(windows: PitWindowResponse[]) {
  vi.mocked(usePitWindow).mockReturnValue({
    data: windows,
    isLoading: false,
  } as unknown as ReturnType<typeof usePitWindow>)
}

describe("PitWindowCard", () => {
  it("renders the pit window lap range correctly", () => {
    mockWindows([buildWindow()])

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("Lap 22–26")).toBeInTheDocument()
  })

  // PitWindowResponse carries no standalone "confidence" field — the closest
  // real analogue is shap_explanation, the SHAP-derived confidence/rationale
  // text formatShapExplanation renders in PitWindowCard.
  it("shows the SHAP explanation text when shap_explanation is provided", () => {
    mockWindows([
      buildWindow({
        shap_explanation: [
          { feature_name: "tyre_age_laps", value: 18, contribution: 0.42, direction: "+" },
        ],
      }),
    ])

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText(/Tyre age is the primary factor/)).toBeInTheDocument()
  })

  it("handles a missing/null shap_explanation gracefully", () => {
    mockWindows([buildWindow({ shap_explanation: null })])

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("Lap 22–26")).toBeInTheDocument()
    expect(screen.queryByText(/primary factor/)).not.toBeInTheDocument()
  })

  it("shows the no-window empty state when there is no predicted window", () => {
    mockWindows([])

    render(<PitWindowCard sessionId="session-1" driverId="driver-1" />)

    expect(screen.getByText("No pit window predicted.")).toBeInTheDocument()
  })
})
