import { useMemo } from "react"
import { TitilliumWeb_400Regular } from "@expo-google-fonts/titillium-web/400Regular"
import { DashPathEffect, Line as SkiaLine, Text as SkiaText, useFont, vec } from "@shopify/react-native-skia"
import { Text, View } from "react-native"
import { CartesianChart, Line, Scatter } from "victory-native"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { formatLapTime, getCompoundColor } from "@/utils/formatters"
import type { LapCompletedEvent, LapDataResponse } from "@/types"

interface LapTimeChartProps {
  sessionId: string | null
  driverId: string | null
  // The live tab's lap-completion events, passed in rather than read here:
  // on mobile every useLiveTelemetry call opens its own WebSocket.
  lapsByDriver: Record<string, LapCompletedEvent>
}

// victory-native takes one data array with fixed y keys, so each compound has
// a column. A stint's line runs through its compound's column; the lap before
// a tyre change is written into both compounds' columns, which joins the two
// lines like web's bridge point. Rows of other compounds leave a column null,
// and with connectMissingData off that ends the line, so two stints on one
// compound are not joined across the stint between them.
const COMPOUND_KEYS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN"] as const
type CompoundKey = (typeof COMPOUND_KEYS)[number]

type ChartRow = { lap: number } & Record<CompoundKey, number | null>

function compoundKey(compound: string): CompoundKey {
  const upper = compound.toUpperCase()
  return (COMPOUND_KEYS as readonly string[]).includes(upper) ? (upper as CompoundKey) : "UNKNOWN"
}

function emptyRow(lap: number): ChartRow {
  return { lap, SOFT: null, MEDIUM: null, HARD: null, INTERMEDIATE: null, WET: null, UNKNOWN: null }
}

// Laps without a time are left out, where web's connectNulls drew straight
// over them, so a stint's line still runs across them.
function buildChartRows(laps: LapDataResponse[]): { rows: ChartRow[]; compounds: CompoundKey[] } {
  const timed = laps.filter((lap) => lap.lap_time_seconds !== null)
  const compounds: CompoundKey[] = []
  const rows: ChartRow[] = []
  let previousKey: CompoundKey | null = null
  for (const lap of timed) {
    const key = compoundKey(lap.compound)
    if (!compounds.includes(key)) compounds.push(key)
    const previousRow = rows[rows.length - 1]
    if (previousRow && previousKey !== null && previousKey !== key) {
      previousRow[key] = previousRow[previousKey]
    }
    const row = emptyRow(lap.lap_number)
    row[key] = lap.lap_time_seconds
    rows.push(row)
    previousKey = key
  }
  return { rows, compounds }
}

function findCompoundChangeLaps(laps: LapDataResponse[]): number[] {
  const changeLaps: number[] = []
  for (let i = 1; i < laps.length; i++) {
    if (laps[i].compound !== laps[i - 1].compound) changeLaps.push(laps[i].lap_number)
  }
  return changeLaps
}

const CHART_HEIGHT = 260
const AXIS_LABEL_COLOR = "#9ca3af"
const AXIS_LINE_COLOR = "rgba(255,255,255,0.1)"
const PIT_MARKER_COLOR = "#9ca3af"

// RN port of web/src/components/telemetry/LapTimeChart.tsx: the selected
// driver's lap times by lap number, the line coloured by compound, with a
// dashed "Pit" marker at each tyre change. During a live race or replay only
// laps up to the driver's latest lap event show; with no event for the driver
// (a completed race) every lap shows, as on web. victory-native has no
// reference line, so the markers are Skia lines drawn with the chart's x scale.
// No tooltip, as on mobile's other charts.
export function LapTimeChart({ sessionId, driverId, lapsByDriver }: LapTimeChartProps) {
  const font = useFont(TitilliumWeb_400Regular, 10)
  const { data, isLoading } = useDriverLaps(sessionId, driverId)
  const liveEvent = driverId ? lapsByDriver[driverId] : undefined

  const laps = useMemo(() => {
    const allLaps = [...(data?.items ?? [])].sort((a, b) => a.lap_number - b.lap_number)
    if (liveEvent === undefined) return allLaps
    return allLaps.filter((lap) => lap.lap_number <= liveEvent.lap_number)
  }, [data, liveEvent])

  const { rows, compounds } = useMemo(() => buildChartRows(laps), [laps])
  const changeLaps = useMemo(() => findCompoundChangeLaps(laps), [laps])

  if (!driverId) {
    return (
      <View className="h-64 items-center justify-center">
        <Text className="text-center text-sm text-muted">
          Tap a driver in Timing to see their lap times.
        </Text>
      </View>
    )
  }

  if (isLoading) {
    return <View className="h-64 w-full rounded-md bg-surface" />
  }

  if (rows.length === 0) {
    return (
      <View className="h-64 items-center justify-center">
        <Text className="text-sm text-muted">No laps recorded yet for this driver.</Text>
      </View>
    )
  }

  return (
    <View>
      <View
        style={{ height: CHART_HEIGHT }}
        accessible
        accessibilityLabel={`Lap times by lap, coloured by compound: ${compounds.join(", ")}`}
      >
        <CartesianChart
          data={rows}
          xKey="lap"
          yKeys={[...COMPOUND_KEYS]}
          domainPadding={{ top: 20, bottom: 12, left: 8, right: 8 }}
          axisOptions={{
            font,
            labelColor: AXIS_LABEL_COLOR,
            lineColor: AXIS_LINE_COLOR,
            formatXLabel: (value) => String(Math.round(value)),
            formatYLabel: (value) => formatLapTime(value),
          }}
        >
          {({ points, xScale, chartBounds }) => (
            <>
              {changeLaps.map((lapNumber) => {
                const x = xScale(lapNumber)
                return (
                  <SkiaLine
                    key={`pit-${lapNumber}`}
                    p1={vec(x, chartBounds.top)}
                    p2={vec(x, chartBounds.bottom)}
                    color={PIT_MARKER_COLOR}
                    strokeWidth={1}
                  >
                    <DashPathEffect intervals={[4, 4]} />
                  </SkiaLine>
                )
              })}
              {font &&
                changeLaps.map((lapNumber) => (
                  <SkiaText
                    key={`pit-label-${lapNumber}`}
                    x={xScale(lapNumber) - 6}
                    // Inside the top domain padding, above the highest point.
                    y={chartBounds.top + 10}
                    text="Pit"
                    font={font}
                    color={PIT_MARKER_COLOR}
                  />
                ))}
              {compounds.map((key) => (
                <Line key={`line-${key}`} points={points[key]} color={getCompoundColor(key)} strokeWidth={2} />
              ))}
              {compounds.map((key) => (
                <Scatter key={`dots-${key}`} points={points[key]} color={getCompoundColor(key)} radius={3} />
              ))}
            </>
          )}
        </CartesianChart>
      </View>
      <Text className="mt-1 text-center text-xs text-muted">Lap</Text>
    </View>
  )
}
