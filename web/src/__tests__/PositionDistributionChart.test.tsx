import type { ReactNode } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { PositionDistributionChart } from "@/components/strategy/PositionDistributionChart"
import type { SimulatedRaceOutcome } from "@/types"

// Same convention as LapTimeChart.test.tsx: recharts' ResponsiveContainer
// needs a real ResizeObserver (unavailable in jsdom), so a real render never
// reaches the <Bar> elements. Standing in for the module with plain divs
// that surface each <Bar>'s dataKey/name as DOM attributes tests exactly
// what this component computes — the position-union/zero-fill/label logic —
// without depending on recharts' layout engine.
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  BarChart: ({ children, data }: { children: ReactNode; data: Record<string, unknown>[] }) => (
    <div data-testid="bar-chart" data-rows={JSON.stringify(data)}>
      {children}
    </div>
  ),
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  Legend: () => <div data-testid="legend" />,
  Bar: ({ dataKey, name, fill }: { dataKey: string; name: string; fill: string }) => (
    <div data-testid="bar" data-key={dataKey} data-name={name} data-fill={fill} />
  ),
}))

function buildStrategy(overrides: Partial<SimulatedRaceOutcome> = {}): SimulatedRaceOutcome {
  return {
    pit_laps: [25],
    compounds: ["HARD"],
    label: null,
    predicted_finish_time: 5000,
    position_gain_loss: 0,
    mean_position: 6.5,
    position_probabilities: [
      { position: 6, probability: 0.4 },
      { position: 7, probability: 0.6 },
    ],
    confidence_interval: [4990, 5010],
    explanation: {
      pit_cost_seconds: 22,
      drivers_overtaken: [],
      remaining_laps: 20,
      fresh_tyre_gain_per_lap: 0.3,
      total_recoverable_seconds: 6,
    },
    ...overrides,
  }
}

describe("PositionDistributionChart", () => {
  it("returns null with no strategies", () => {
    const { container } = render(
      <PositionDistributionChart strategies={[]} startingPosition={6} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it("builds one bar series per strategy with a unique key and its display label", () => {
    const strategies = [
      buildStrategy({ label: "Pit lap 25" }),
      buildStrategy({ label: "Pit lap 30", pit_laps: [30] }),
    ]
    render(<PositionDistributionChart strategies={strategies} startingPosition={6} />)

    const bars = screen.getAllByTestId("bar")
    expect(bars).toHaveLength(2)
    expect(bars[0]).toHaveAttribute("data-key", "s0")
    expect(bars[0]).toHaveAttribute("data-name", "Pit lap 25")
    expect(bars[1]).toHaveAttribute("data-key", "s1")
    expect(bars[1]).toHaveAttribute("data-name", "Pit lap 30")
    // Colors assigned in fixed categorical order, one per strategy index.
    expect(bars[0].getAttribute("data-fill")).not.toEqual(bars[1].getAttribute("data-fill"))
  })

  it("falls back to a synthesized label when a strategy has no label (single-plan path)", () => {
    const strategies = [buildStrategy({ label: null, pit_laps: [22, 41] })]
    render(<PositionDistributionChart strategies={strategies} startingPosition={6} />)

    expect(screen.getByTestId("bar")).toHaveAttribute("data-name", "Plan 1 (L22, L41)")
  })

  it("shows a legend only for 2+ series, never for a single strategy", () => {
    const { rerender } = render(
      <PositionDistributionChart strategies={[buildStrategy()]} startingPosition={6} />,
    )
    expect(screen.queryByTestId("legend")).not.toBeInTheDocument()

    rerender(
      <PositionDistributionChart
        strategies={[buildStrategy(), buildStrategy({ pit_laps: [30] })]}
        startingPosition={6}
      />,
    )
    expect(screen.getByTestId("legend")).toBeInTheDocument()
  })

  it("zero-fills a position missing from one strategy but present in another", () => {
    const strategies = [
      buildStrategy({
        label: "A",
        position_probabilities: [{ position: 5, probability: 1.0 }],
      }),
      buildStrategy({
        label: "B",
        pit_laps: [30],
        position_probabilities: [{ position: 6, probability: 1.0 }],
      }),
    ]
    render(<PositionDistributionChart strategies={strategies} startingPosition={6} />)

    const rows = JSON.parse(
      screen.getByTestId("bar-chart").getAttribute("data-rows") ?? "[]",
    ) as Record<string, unknown>[]
    // Union of positions {5, 6}, ascending — each row carries both series'
    // keys, zero for whichever strategy has no entry at that position.
    expect(rows).toEqual([
      { positionLabel: "P5", s0: 100, s1: 0 },
      { positionLabel: "P6", s0: 0, s1: 100 },
    ])
  })

  it("renders the risk/reward table with P(Gain)/P(Hold)/P(Lose) relative to startingPosition", () => {
    // starting_position=6: P4/P5 count as gain, P6 as hold, P7/P8 as lose.
    const strategy = buildStrategy({
      label: "Pit lap 25",
      mean_position: 6.2,
      position_probabilities: [
        { position: 5, probability: 0.2 },
        { position: 6, probability: 0.5 },
        { position: 7, probability: 0.3 },
      ],
    })
    render(<PositionDistributionChart strategies={[strategy]} startingPosition={6} />)

    expect(screen.getByText("6.20")).toBeInTheDocument() // Mean Pos
    expect(screen.getByText("20%")).toBeInTheDocument() // P(Gain)
    expect(screen.getByText("50%")).toBeInTheDocument() // P(Hold)
    expect(screen.getByText("30%")).toBeInTheDocument() // P(Lose)
  })

  it("computes P(Hold) as 0% when startingPosition has no entry in position_probabilities", () => {
    const strategy = buildStrategy({
      position_probabilities: [
        { position: 4, probability: 0.5 },
        { position: 8, probability: 0.5 },
      ],
    })
    render(<PositionDistributionChart strategies={[strategy]} startingPosition={6} />)

    // Both P(Gain) and P(Lose) show 50%, P(Hold) shows 0% — three distinct
    // cells rendering the same two percentages plus a zero confirms the
    // split, not just that some percentage renders somewhere.
    expect(screen.getAllByText("50%")).toHaveLength(2)
    expect(screen.getByText("0%")).toBeInTheDocument()
  })
})
