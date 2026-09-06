import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { CHART_TOOLTIP_STYLE, SCENARIO_SERIES_COLORS } from "@/utils/constants"
import { formatRaceTime } from "@/utils/formatters"
import type { SimulatedRaceOutcome } from "@/types"

interface PositionDistributionChartProps {
  strategies: SimulatedRaceOutcome[]
  // The requester's real track position before any strategy's laps run —
  // SimulateStrategyResponse.starting_position, shared across every
  // strategy. Used to split each strategy's position_probabilities into
  // P(Gain)/P(Hold)/P(Lose) relative to where the driver actually started.
  startingPosition: number
}

interface StrategySeries {
  key: string
  label: string
  color: string
}

function buildSeries(strategies: SimulatedRaceOutcome[]): StrategySeries[] {
  return strategies.map((strategy, index) => ({
    // A stable, collision-free key independent of label content (two
    // scenarios can share a label, e.g. both left blank) — recharts'
    // `name` prop carries the display text separately.
    key: `s${index}`,
    label: strategy.label ?? `Plan ${index + 1} (L${strategy.pit_laps.join(", L")})`,
    color: SCENARIO_SERIES_COLORS[index % SCENARIO_SERIES_COLORS.length],
  }))
}

// Restores the vision's original ask (see docs/core-feature-rebuild-whatif-
// simulator.md): "67% chance of finishing P2, 18% chance P1, 15% chance P3"
// per scenario, visualized side by side. race_simulator.simulate_race
// already computes this full distribution from 1000 real simulations for
// every strategy — this just renders what backend/schemas/simulate_schema.py's
// SimulatedRaceOutcome.position_probabilities already carries.
export function PositionDistributionChart({
  strategies,
  startingPosition,
}: PositionDistributionChartProps) {
  if (strategies.length === 0) return null

  const series = buildSeries(strategies)

  // Union of every position ANY strategy has a non-zero probability for,
  // ascending — position_probabilities is sparse per strategy (see its own
  // backend docstring), so a position missing from one strategy's list but
  // present in another's still needs a (zero-valued) bar in that strategy's
  // group for the chart to compare them fairly at every position.
  const positions = Array.from(
    new Set(strategies.flatMap((strategy) => strategy.position_probabilities.map((p) => p.position))),
  ).sort((a, b) => a - b)

  const chartData = positions.map((position) => {
    const row: Record<string, number | string> = { positionLabel: `P${position}` }
    strategies.forEach((strategy, index) => {
      const match = strategy.position_probabilities.find((p) => p.position === position)
      // Percentage, one decimal — matches the vision's own "67% chance" framing.
      row[series[index].key] = match ? Math.round(match.probability * 1000) / 10 : 0
    })
    return row
  })

  return (
    <div className="space-y-3">
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid vertical={false} className="stroke-border" />
          <XAxis dataKey="positionLabel" className="text-xs fill-muted-foreground" />
          <YAxis
            className="text-xs fill-muted-foreground"
            tickFormatter={(value: number) => `${value}%`}
            label={{ value: "Probability", angle: -90, position: "insideLeft" }}
          />
          <Tooltip
            formatter={(value) => [`${value}%`, undefined] as [string, undefined]}
            {...CHART_TOOLTIP_STYLE}
          />
          {/* Legend only for >=2 series — a single strategy's identity is
              already carried by the card/section title above it (dataviz
              convention: a lone series needs no legend box). */}
          {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              fill={s.color}
              radius={[4, 4, 0, 0]}
              isAnimationActive={false}
              maxBarSize={24}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="px-2 py-1 font-medium">Strategy</th>
              <th className="px-2 py-1 text-right font-medium">Mean Pos</th>
              <th className="px-2 py-1 text-right font-medium">P(Gain)</th>
              <th className="px-2 py-1 text-right font-medium">P(Hold)</th>
              <th className="px-2 py-1 text-right font-medium">P(Lose)</th>
              <th className="px-2 py-1 text-right font-medium">Finish Time Range</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map((strategy, index) => {
              const gain = strategy.position_probabilities
                .filter((p) => p.position < startingPosition)
                .reduce((sum, p) => sum + p.probability, 0)
              const hold =
                strategy.position_probabilities.find((p) => p.position === startingPosition)
                  ?.probability ?? 0
              const lose = strategy.position_probabilities
                .filter((p) => p.position > startingPosition)
                .reduce((sum, p) => sum + p.probability, 0)
              return (
                <tr
                  key={index}
                  className={index % 2 === 0 ? "bg-row-void" : "bg-row-recede"}
                >
                  <td className="px-2 py-1.5">
                    <div className="flex items-center gap-2">
                      <span
                        className="h-3 w-1 flex-shrink-0 rounded-full"
                        style={{ backgroundColor: series[index].color }}
                      />
                      <span className="truncate">{series[index].label}</span>
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">
                    {strategy.mean_position.toFixed(2)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-[#10B981]">
                    {(gain * 100).toFixed(0)}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted-foreground">
                    {(hold * 100).toFixed(0)}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-[#EF4444]">
                    {(lose * 100).toFixed(0)}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted-foreground">
                    {formatRaceTime(strategy.confidence_interval[0])}–
                    {formatRaceTime(strategy.confidence_interval[1])}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
