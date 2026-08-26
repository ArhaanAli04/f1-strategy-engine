import type { ReactNode } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { LapTimeChart } from "@/components/telemetry/LapTimeChart"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import type { LapCompletedEvent, LapDataResponse } from "@/types"

vi.mock("@/hooks/useDriverLaps", () => ({ useDriverLaps: vi.fn() }))
vi.mock("@/hooks/useLiveTelemetry", () => ({ useLiveTelemetry: vi.fn() }))

// recharts' ResponsiveContainer needs a real ResizeObserver (unavailable in
// jsdom, and not polyfilled in src/test/setup.ts) to ever render its
// children, so a real render would never reach the <Line> elements below
// regardless of what data they're given. Standing in for the whole module
// with simple divs that surface each <Line>'s `data` prop as a DOM attribute
// tests exactly what this checkpoint cares about — what lap data actually
// reaches the chart — without depending on recharts' internal layout engine.
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  ReferenceLine: () => null,
  Line: ({ data, name }: { data: { lap_number: number }[]; name: string }) => (
    <div
      data-testid="lap-line"
      data-compound={name}
      data-laps={data.map((point) => point.lap_number).join(",")}
    />
  ),
}))

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

function buildLaps(count: number): LapDataResponse[] {
  return Array.from({ length: count }, (_, i) =>
    buildLap({ lap_number: i + 1, lap_time_seconds: 90.0 + i, id: `lap-${i + 1}` }),
  )
}

function mockDriverLaps(laps: LapDataResponse[], isLoading = false) {
  vi.mocked(useDriverLaps).mockReturnValue({
    data: { items: laps, total: laps.length, page: 1, page_size: laps.length },
    isLoading,
  } as unknown as ReturnType<typeof useDriverLaps>)
}

function mockLiveTelemetry(lapsByDriver: Record<string, Partial<LapCompletedEvent>>) {
  vi.mocked(useLiveTelemetry).mockReturnValue({
    lapsByDriver: lapsByDriver as Record<string, LapCompletedEvent>,
    readyState: "open",
    staleConnection: false,
  })
}

describe("LapTimeChart", () => {
  it("only renders laps up to the driver's current WS-reported lap, not the full fetched dataset", () => {
    mockDriverLaps(buildLaps(20))
    mockLiveTelemetry({ "driver-1": { lap_number: 5 } })

    render(<LapTimeChart sessionId="session-1" driverId="driver-1" />)

    const lines = screen.getAllByTestId("lap-line")
    const renderedLapNumbers = lines.flatMap((line) =>
      (line.getAttribute("data-laps") ?? "")
        .split(",")
        .filter(Boolean)
        .map(Number),
    )

    expect(renderedLapNumbers.length).toBeGreaterThan(0)
    expect(Math.max(...renderedLapNumbers)).toBe(5)
    expect(renderedLapNumbers.every((lap) => lap <= 5)).toBe(true)
    // Laps 6-20 must never reach the chart.
    expect(renderedLapNumbers).not.toContain(6)
    expect(renderedLapNumbers).not.toContain(20)
  })

  it("shows the full fetched dataset when no WS data has ever arrived (historical viewing, no live/replay active)", () => {
    mockDriverLaps(buildLaps(20))
    mockLiveTelemetry({}) // no entry for driver-1 at all — not "no WS server", genuinely never arrived

    render(<LapTimeChart sessionId="session-1" driverId="driver-1" />)

    const lines = screen.getAllByTestId("lap-line")
    const renderedLapNumbers = lines.flatMap((line) =>
      (line.getAttribute("data-laps") ?? "")
        .split(",")
        .filter(Boolean)
        .map(Number),
    )

    expect(renderedLapNumbers).toContain(1)
    expect(renderedLapNumbers).toContain(20)
    expect(new Set(renderedLapNumbers).size).toBe(20)
  })

  it("prompts for a driver selection when driverId is null", () => {
    mockDriverLaps([])
    mockLiveTelemetry({})

    render(<LapTimeChart sessionId="session-1" driverId={null} />)

    expect(
      screen.getByText("Select a driver in the timing tower to see their lap times."),
    ).toBeInTheDocument()
  })
})
