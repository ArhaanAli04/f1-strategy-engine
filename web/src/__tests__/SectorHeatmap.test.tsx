import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { SectorHeatmap } from "@/components/telemetry/SectorHeatmap"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { formatLapTime } from "@/utils/formatters"
import type { DriverResponse, LapCompletedEvent, LapDataResponse } from "@/types"

vi.mock("@/hooks/useDrivers", () => ({ useDrivers: vi.fn() }))
vi.mock("@/hooks/useSessionGaps", () => ({ useSessionGaps: vi.fn() }))
vi.mock("@/hooks/useLiveTelemetry", () => ({ useLiveTelemetry: vi.fn() }))

// SectorHeatmap fetches each driver's laps via useQueries(driverLapsQueryOptions(...))
// directly (no wrapping custom hook to mock, unlike LapTimeChart's useDriverLaps) — this
// mock returns each query's canned data keyed by the driverId baked into its queryKey
// (["driver", "laps", sessionId, driverId] — see hooks/useDriverLaps.ts), set per-test via
// lapsByDriverFixture below.
let lapsByDriverFixture: Record<string, LapDataResponse[]> = {}

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQueries: ({ queries }: { queries: { queryKey: readonly unknown[] }[] }) =>
      queries.map((query) => {
        const driverId = query.queryKey[3] as string
        return { data: { items: lapsByDriverFixture[driverId] ?? [] }, isLoading: false }
      }),
  }
})

function buildDriver(id: string, code: string): DriverResponse {
  return {
    id,
    code,
    full_name: code,
    nationality: "GBR",
    date_of_birth: null,
    contracts: [
      {
        id: `contract-${id}`,
        driver_id: id,
        team_id: `team-${id}`,
        season: 2026,
        team: { id: `team-${id}`, name: `${code} Racing`, constructor_id: `c-${id}`, color_hex: "#fff" },
      },
    ],
  }
}

function buildLap(overrides: Partial<LapDataResponse> = {}): LapDataResponse {
  return {
    id: `lap-${overrides.lap_number ?? 1}`,
    session_id: "session-1",
    driver_id: "driver-1",
    lap_number: 1,
    lap_time_seconds: 90.0,
    compound: "MEDIUM",
    tyre_age_laps: 1,
    is_valid: true,
    sector1_seconds: 30.0,
    sector2_seconds: 30.0,
    sector3_seconds: 30.0,
    created_at: "2026-08-26T00:00:00Z",
    sector_times: [],
    ...overrides,
  }
}

function buildLaps(count: number, driverId: string): LapDataResponse[] {
  // Lap time strictly decreases by 1s per lap so each lap's formatted text is
  // distinct and unambiguously identifiable in assertions.
  return Array.from({ length: count }, (_, i) =>
    buildLap({
      id: `lap-${driverId}-${i + 1}`,
      driver_id: driverId,
      lap_number: i + 1,
      lap_time_seconds: 100.0 - i,
    }),
  )
}

function mockLiveTelemetry(lapsByDriver: Record<string, Partial<LapCompletedEvent>>) {
  vi.mocked(useLiveTelemetry).mockReturnValue({
    lapsByDriver: lapsByDriver as Record<string, LapCompletedEvent>,
    readyState: "open",
    staleConnection: false,
  })
}

describe("SectorHeatmap", () => {
  it("only shows a driver's row up to their current WS-reported lap, not the full fetched dataset", () => {
    const driver = buildDriver("driver-1", "HUL")
    vi.mocked(useDrivers).mockReturnValue({
      data: [driver],
    } as unknown as ReturnType<typeof useDrivers>)
    vi.mocked(useSessionGaps).mockReturnValue({
      data: undefined,
    } as unknown as ReturnType<typeof useSessionGaps>)
    lapsByDriverFixture = { "driver-1": buildLaps(20, "driver-1") }
    mockLiveTelemetry({ "driver-1": { lap_number: 5 } })

    render(<SectorHeatmap sessionId="session-1" />)

    // Latest lap within the filtered set is lap 5 (lap_time_seconds = 96.0),
    // not lap 20 (lap_time_seconds = 81.0) from the full fetched dataset.
    expect(screen.getAllByText(formatLapTime(96.0)).length).toBeGreaterThan(0)
    expect(screen.queryByText(formatLapTime(81.0))).not.toBeInTheDocument()
  })

  it("shows the driver's full fetched dataset when no WS data has ever arrived (historical viewing, no live/replay active)", () => {
    const driver = buildDriver("driver-1", "HUL")
    vi.mocked(useDrivers).mockReturnValue({
      data: [driver],
    } as unknown as ReturnType<typeof useDrivers>)
    vi.mocked(useSessionGaps).mockReturnValue({
      data: undefined,
    } as unknown as ReturnType<typeof useSessionGaps>)
    lapsByDriverFixture = { "driver-1": buildLaps(20, "driver-1") }
    mockLiveTelemetry({}) // no entry for driver-1 at all — never arrived, not "server-side" empty

    render(<SectorHeatmap sessionId="session-1" />)

    expect(screen.getByText("HUL")).toBeInTheDocument()
    // Latest lap across the FULL 20-lap dataset is lap 20 (lap_time_seconds =
    // 81.0) — the heatmap must fall back to the complete race, not lap 5.
    expect(screen.getAllByText(formatLapTime(81.0)).length).toBeGreaterThan(0)
    expect(screen.queryByText(formatLapTime(96.0))).not.toBeInTheDocument()
  })
})
