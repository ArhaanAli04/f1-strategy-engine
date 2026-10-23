import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { UndercutThreatPanel } from "@/components/strategy/UndercutThreatPanel"
import { useCurrentLapHistoryEntry, useUndercut } from "@/hooks/useStrategy"
import type { UndercutThreatResponse } from "@/types"

// Not replaying: the panel renders /undercut's answers (useUndercut) for the
// cars ahead of and behind the selected driver in the timing tower.
vi.mock("@/hooks/useStrategy", () => ({
  useCurrentLapHistoryEntry: vi.fn(),
  useUndercut: vi.fn(),
}))

vi.mock("@/hooks/useSessionGaps", () => ({
  useSessionGaps: vi.fn(() => ({
    data: {
      gaps: [
        { driver_id: "bot", position: 1 },
        { driver_id: "alo", position: 2 },
        { driver_id: "str", position: 3 },
      ],
    },
  })),
}))

vi.mock("@/hooks/useDrivers", () => ({
  useDrivers: vi.fn(() => ({
    data: [
      { id: "bot", code: "BOT", contracts: [] },
      { id: "alo", code: "ALO", contracts: [] },
      { id: "str", code: "STR", contracts: [] },
    ],
  })),
}))

function undercut(overrides: Partial<UndercutThreatResponse>): UndercutThreatResponse {
  return {
    target_driver_id: "bot",
    probability_pit_now_gains_position: 0.42,
    projected_gap_seconds: 1.5,
    n_laps_projected: 5,
    recommended_action: "STAY OUT",
    ...overrides,
  }
}

// useUndercut(sessionId, driverId, target): the opportunity row asks for the
// selected driver against the car ahead, the threat row for the car behind
// against the selected driver.
function mockUndercuts(opportunity: UndercutThreatResponse, threat: UndercutThreatResponse) {
  vi.mocked(useUndercut).mockImplementation((_session, driverId) => {
    const data = driverId === "alo" ? opportunity : threat
    return { data, isLoading: false } as ReturnType<typeof useUndercut>
  })
}

beforeEach(() => {
  vi.mocked(useCurrentLapHistoryEntry).mockReturnValue({
    entry: null,
    isReplayActive: false,
    isLoading: false,
  })
})

describe("UndercutThreatPanel", () => {
  it("shows a retired neighbour as retired instead of a probability", () => {
    // Belgian GP 2026: STR, out on lap 25, behind ALO in the timing tower.
    mockUndercuts(
      undercut({ target_driver_id: "bot" }),
      undercut({
        target_driver_id: "alo",
        probability_pit_now_gains_position: 0,
        projected_gap_seconds: 0,
        n_laps_projected: 0,
        retired_driver_id: "str",
      }),
    )

    render(<UndercutThreatPanel sessionId="session-1" driverId="alo" />)

    expect(screen.getByText(/retired — no undercut/)).toBeInTheDocument()
    // The car-ahead row is a normal projection.
    expect(screen.getByText("42% gain probability")).toBeInTheDocument()
    expect(screen.getAllByText(/gain probability/)).toHaveLength(1)
    expect(screen.queryByText("PIT NOW")).not.toBeInTheDocument()
  })

  it("shows both projections when nobody has retired", () => {
    mockUndercuts(undercut({}), undercut({ probability_pit_now_gains_position: 0.1 }))

    render(<UndercutThreatPanel sessionId="session-1" driverId="alo" />)

    expect(screen.getByText("42% gain probability")).toBeInTheDocument()
    expect(screen.getByText("10% gain probability")).toBeInTheDocument()
    expect(screen.queryByText(/retired/)).not.toBeInTheDocument()
  })
})
