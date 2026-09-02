import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { LiveTimingTower } from "@/components/telemetry/LiveTimingTower"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import type { DriverGap, DriverResponse } from "@/types"

vi.mock("@/hooks/useDrivers", () => ({ useDrivers: vi.fn() }))
vi.mock("@/hooks/useSessionGaps", () => ({ useSessionGaps: vi.fn() }))
vi.mock("@/hooks/useLiveTelemetry", () => ({ useLiveTelemetry: vi.fn() }))
vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  // LiveTimingTower's per-driver lap-history fallback isn't under test here
  // — starving it keeps this test off the network/WS entirely.
  return { ...actual, useQueries: () => [] }
})

// 2026 season grid — 22 drivers (11 teams), not 20.
const DRIVER_COUNT = 22

function buildDrivers(count: number): DriverResponse[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `driver-${i + 1}`,
    code: `D${i + 1}`,
    full_name: `Driver ${i + 1}`,
    nationality: "GBR",
    date_of_birth: null,
    contracts: [],
  }))
}

function buildGaps(count: number): DriverGap[] {
  return Array.from({ length: count }, (_, i) => ({
    driver_id: `driver-${i + 1}`,
    lap_number: 10,
    position: i + 1,
    gap_to_ahead_seconds: i === 0 ? 0 : 1.234,
    gap_to_behind_seconds: 1.234,
    laps_behind: 0,
  }))
}

function mockGaps(gaps: DriverGap[]) {
  vi.mocked(useSessionGaps).mockReturnValue({
    data: { session_id: "session-1", gaps },
    isLoading: false,
  } as unknown as ReturnType<typeof useSessionGaps>)
}

describe("LiveTimingTower", () => {
  beforeEach(() => {
    vi.mocked(useDrivers).mockReturnValue({
      data: buildDrivers(DRIVER_COUNT),
    } as unknown as ReturnType<typeof useDrivers>)
    vi.mocked(useLiveTelemetry).mockReturnValue({
      lapsByDriver: {},
      readyState: "closed",
      staleConnection: false,
    })
  })

  it("renders 22 driver rows with mock gap data", () => {
    mockGaps(buildGaps(DRIVER_COUNT))

    render(<LiveTimingTower sessionId="session-1" />)

    expect(screen.getAllByRole("button")).toHaveLength(DRIVER_COUNT)
  })

  it("shows Leader for the first row instead of a gap value", () => {
    mockGaps(buildGaps(DRIVER_COUNT))

    render(<LiveTimingTower sessionId="session-1" />)

    expect(screen.getByText("Leader")).toBeInTheDocument()
  })

  it("shows the empty state when gaps array is empty", () => {
    mockGaps([])

    render(<LiveTimingTower sessionId="session-1" />)

    expect(screen.getByText("No live race session active")).toBeInTheDocument()
    expect(screen.queryAllByRole("button")).toHaveLength(0)
  })
})
