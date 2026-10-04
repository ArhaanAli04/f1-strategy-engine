import { render, screen, within } from "@testing-library/react"
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

  it("shows the tower's current tyre over the last completed lap's, falling back to it", () => {
    // D1 has pitted onto HARD: its last completed lap (the in-lap) was on
    // MEDIUM. D2's tower entry carries no tyre, so its last lap's is used.
    const gaps = buildGaps(2)
    gaps[0] = { ...gaps[0], compound: "HARD" }
    mockGaps(gaps)
    const lap = (driverId: string) => ({
      driver_id: driverId,
      session_id: "session-1",
      lap_number: 17,
      lap_time_seconds: 115.7,
      compound: "MEDIUM",
      sector1_seconds: null,
      sector2_seconds: null,
      sector3_seconds: null,
      speed_kmh: null,
      throttle_pct: null,
      brake: null,
      gear: null,
      drs: null,
    })
    vi.mocked(useLiveTelemetry).mockReturnValue({
      lapsByDriver: { "driver-1": lap("driver-1"), "driver-2": lap("driver-2") },
      readyState: "open",
      staleConnection: false,
    })

    render(<LiveTimingTower sessionId="session-1" />)

    const rows = screen.getAllByRole("button")
    expect(within(rows[0]).getByLabelText("HARD")).toBeInTheDocument()
    expect(within(rows[1]).getByLabelText("MEDIUM")).toBeInTheDocument()
  })

  it("shows the empty state when gaps array is empty", () => {
    mockGaps([])

    render(<LiveTimingTower sessionId="session-1" />)

    expect(screen.getByText("No live race session active")).toBeInTheDocument()
    expect(screen.queryAllByRole("button")).toHaveLength(0)
  })
})
