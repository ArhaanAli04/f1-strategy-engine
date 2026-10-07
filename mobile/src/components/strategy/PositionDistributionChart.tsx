import { TitilliumWeb_400Regular } from "@expo-google-fonts/titillium-web/400Regular"
import { useFont } from "@shopify/react-native-skia"
import { useState } from "react"
import { Text, View } from "react-native"
import { BarGroup, CartesianChart } from "victory-native"
import { SCENARIO_SERIES_COLORS } from "@/utils/constants"
import type { SimulatedRaceOutcome } from "@/types"

interface PositionDistributionChartProps {
  strategies: SimulatedRaceOutcome[]
  // The requester's real track position before any strategy's laps run —
  // SimulateStrategyResponse.starting_position, shared across every
  // strategy. Splits each strategy's position_probabilities into
  // P(Gain)/P(Hold)/P(Lose) relative to where the driver started.
  startingPosition: number
}

// victory-native takes one data array with fixed y keys: one column per
// strategy (at most 4, the backend's scenarios cap), only the first
// strategies.length of them drawn.
const SERIES_KEYS = ["s0", "s1", "s2", "s3"] as const
type SeriesKey = (typeof SERIES_KEYS)[number]
type ChartRow = { position: string } & Record<SeriesKey, number>

const CHART_HEIGHT_PX = 220
const BETWEEN_GROUP_PADDING = 0.3
const EDGE_PADDING_MIN_PX = 16

// BarGroup centres each group of bars on its x point and makes it
// (1 - BETWEEN_GROUP_PADDING) * plot width / groups wide, so the first and
// last groups need half that much room at the chart's edges. With 16 px the
// edge bars were cut off on the iPhone (2026-10-07: a single P12 bar ran off
// the right edge and a P22 bar wasn't visible). The container's width is a
// little more than the plot's, so this errs on the roomy side.
function edgePadding(containerWidth: number, groupCount: number): number {
  const halfGroup = ((1 - BETWEEN_GROUP_PADDING) * containerWidth) / Math.max(1, groupCount) / 2
  return Math.max(EDGE_PADDING_MIN_PX, Math.ceil(halfGroup))
}
const AXIS_LABEL_COLOR = "#9ca3af"
const AXIS_LINE_COLOR = "rgba(255,255,255,0.1)"
const GAIN_COLOR = "#10B981"
const LOSS_COLOR = "#EF4444"
// Column widths as plain styles: no other mobile file uses the matching
// width classes, and such a class may not apply on the device (see
// mobile/src/README.md).
const MEAN_COLUMN_STYLE = { width: 56 }
const PERCENT_COLUMN_STYLE = { width: 48 }

export function strategyLabel(strategy: SimulatedRaceOutcome, index: number): string {
  return strategy.label ?? `Plan ${index + 1} (L${strategy.pit_laps.join(", L")})`
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <View className="flex-row items-center gap-1.5">
      <View className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color }} />
      <Text numberOfLines={1} className="text-xs text-muted">
        {label}
      </Text>
    </View>
  )
}

// RN port of web/src/components/strategy/PositionDistributionChart.tsx
// (2026-10-07): each strategy's chance of finishing in each position, from
// the same 1000-simulation Monte Carlo run, as grouped bars (one colour per
// strategy, SCENARIO_SERIES_COLORS as on web), then web's Mean Pos /
// P(Gain) / P(Hold) / P(Lose) table. Web's table also repeats the finish-time
// range; on a phone that column didn't fit, and the results summary above
// already shows it. No tooltip, as on mobile's other charts; a swatch legend
// for two or more strategies, as web's.
export function PositionDistributionChart({ strategies, startingPosition }: PositionDistributionChartProps) {
  const font = useFont(TitilliumWeb_400Regular, 10)
  const [chartWidth, setChartWidth] = useState(0)
  if (strategies.length === 0) return null

  const series = strategies.slice(0, SERIES_KEYS.length).map((strategy, index) => ({
    key: SERIES_KEYS[index],
    label: strategyLabel(strategy, index),
    color: SCENARIO_SERIES_COLORS[index % SCENARIO_SERIES_COLORS.length],
  }))

  // Union of every position any strategy has a probability for, ascending
  // (position_probabilities is sparse per strategy), so each strategy gets a
  // (zero) bar at every position and the groups compare fairly. Percentages,
  // one decimal, as on web.
  const positions = Array.from(
    new Set(strategies.flatMap((strategy) => strategy.position_probabilities.map((p) => p.position))),
  ).sort((a, b) => a - b)

  const rows: ChartRow[] = positions.map((position) => {
    const row: ChartRow = { position: `P${position}`, s0: 0, s1: 0, s2: 0, s3: 0 }
    series.forEach((s, index) => {
      const match = strategies[index].position_probabilities.find((p) => p.position === position)
      row[s.key] = match ? Math.round(match.probability * 1000) / 10 : 0
    })
    return row
  })

  return (
    <View className="gap-3">
      <View
        style={{ height: CHART_HEIGHT_PX }}
        onLayout={(event) => setChartWidth(event.nativeEvent.layout.width)}
      >
        <CartesianChart
          data={rows}
          xKey="position"
          yKeys={series.map((s) => s.key)}
          domainPadding={{
            left: edgePadding(chartWidth, rows.length),
            right: edgePadding(chartWidth, rows.length),
            top: 12,
          }}
          axisOptions={{
            font,
            labelColor: AXIS_LABEL_COLOR,
            lineColor: AXIS_LINE_COLOR,
            formatYLabel: (value) => `${value}%`,
          }}
        >
          {({ points, chartBounds }) => (
            <BarGroup
              chartBounds={chartBounds}
              betweenGroupPadding={BETWEEN_GROUP_PADDING}
              withinGroupPadding={0.1}
              roundedCorners={{ topLeft: 3, topRight: 3 }}
            >
              {series.map((s) => (
                <BarGroup.Bar key={s.key} points={points[s.key]} color={s.color} />
              ))}
            </BarGroup>
          )}
        </CartesianChart>
      </View>
      {series.length > 1 && (
        <View className="flex-row flex-wrap items-center justify-center gap-4">
          {series.map((s) => (
            <LegendSwatch key={s.key} color={s.color} label={s.label} />
          ))}
        </View>
      )}

      <View className="gap-0.5">
        <View className="flex-row items-center px-2 py-1">
          <Text className="flex-1 text-[10px] font-medium text-muted">STRATEGY</Text>
          <Text style={MEAN_COLUMN_STYLE} className="text-right text-[10px] font-medium text-muted">MEAN POS</Text>
          <Text style={PERCENT_COLUMN_STYLE} className="text-right text-[10px] font-medium text-muted">GAIN</Text>
          <Text style={PERCENT_COLUMN_STYLE} className="text-right text-[10px] font-medium text-muted">HOLD</Text>
          <Text style={PERCENT_COLUMN_STYLE} className="text-right text-[10px] font-medium text-muted">LOSE</Text>
        </View>
        {strategies.map((strategy, index) => {
          const gain = strategy.position_probabilities
            .filter((p) => p.position < startingPosition)
            .reduce((sum, p) => sum + p.probability, 0)
          const hold =
            strategy.position_probabilities.find((p) => p.position === startingPosition)?.probability ?? 0
          const lose = strategy.position_probabilities
            .filter((p) => p.position > startingPosition)
            .reduce((sum, p) => sum + p.probability, 0)
          const color = SCENARIO_SERIES_COLORS[index % SCENARIO_SERIES_COLORS.length]
          return (
            <View
              key={index}
              className={`flex-row items-center rounded px-2 py-1.5 ${index % 2 === 0 ? "bg-background" : "bg-surface"}`}
            >
              <View className="flex-1 flex-row items-center gap-2">
                <View className="h-3 w-1 rounded-full" style={{ backgroundColor: color }} />
                <Text numberOfLines={1} className="flex-1 text-xs text-foreground">
                  {strategyLabel(strategy, index)}
                </Text>
              </View>
              <Text style={MEAN_COLUMN_STYLE} className="text-right font-mono text-xs text-foreground">
                {strategy.mean_position.toFixed(2)}
              </Text>
              <Text className="text-right font-mono text-xs" style={[PERCENT_COLUMN_STYLE, { color: GAIN_COLOR }]}>
                {(gain * 100).toFixed(0)}%
              </Text>
              <Text style={PERCENT_COLUMN_STYLE} className="text-right font-mono text-xs text-muted">{(hold * 100).toFixed(0)}%</Text>
              <Text className="text-right font-mono text-xs" style={[PERCENT_COLUMN_STYLE, { color: LOSS_COLOR }]}>
                {(lose * 100).toFixed(0)}%
              </Text>
            </View>
          )
        })}
      </View>
    </View>
  )
}
