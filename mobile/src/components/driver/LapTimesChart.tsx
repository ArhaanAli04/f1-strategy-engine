import { useMemo } from "react"
import { TitilliumWeb_400Regular } from "@expo-google-fonts/titillium-web/400Regular"
import { useFont } from "@shopify/react-native-skia"
import { Text, View } from "react-native"
import { CartesianChart, Line, Scatter } from "victory-native"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { formatLapTime, getCompoundColor } from "@/utils/formatters"
import type { LapDataResponse } from "@/types"

interface LapTimesChartProps {
  sessionId: string | null
  driverId: string | null
}

// victory-native takes one data array with a fixed set of y keys, so every
// compound gets a column and each lap fills only its own compound's. Any
// compound outside the five dry/wet tyres goes in UNKNOWN, as getCompoundColor
// colours it.
const COMPOUND_KEYS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN"] as const
type CompoundKey = (typeof COMPOUND_KEYS)[number]

type ChartRow = { tyreAge: number } & Record<CompoundKey, number | null>

function compoundKey(compound: string): CompoundKey {
  const upper = compound.toUpperCase()
  return (COMPOUND_KEYS as readonly string[]).includes(upper) ? (upper as CompoundKey) : "UNKNOWN"
}

// Same series as web's buildCompoundSeries: grouped by compound across the
// whole session (not by stint) and plotted against tyre age, so stints on one
// compound overlay into one degradation curve. One row per lap, sorted by tyre
// age; connectMissingData then joins each compound's laps across the other
// compounds' rows.
function buildChartRows(laps: LapDataResponse[]): { rows: ChartRow[]; compounds: CompoundKey[] } {
  const compounds: CompoundKey[] = []
  const rows: ChartRow[] = []
  for (const lap of laps) {
    if (lap.lap_time_seconds === null) continue
    const key = compoundKey(lap.compound)
    if (!compounds.includes(key)) compounds.push(key)
    const row: ChartRow = {
      tyreAge: lap.tyre_age_laps,
      SOFT: null,
      MEDIUM: null,
      HARD: null,
      INTERMEDIATE: null,
      WET: null,
      UNKNOWN: null,
    }
    row[key] = lap.lap_time_seconds
    rows.push(row)
  }
  rows.sort((a, b) => a.tyreAge - b.tyreAge)
  return { rows, compounds }
}

const CHART_HEIGHT = 260
const AXIS_LABEL_COLOR = "#9ca3af"
const AXIS_LINE_COLOR = "rgba(255,255,255,0.1)"

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <View className="flex-row items-center gap-1.5">
      <View className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
      <Text className="text-xs text-muted">{label}</Text>
    </View>
  )
}

// RN port of web/src/components/driver/LapTimesChart.tsx (lap time by tyre age,
// one line per compound), with victory-native's CartesianChart + Line + Scatter
// in place of Recharts. No tooltip, as on mobile's other charts; the legend is
// a swatch row, as in SectorComparison. Axis labels need a Skia font of their
// own (see SectorComparison).
export function LapTimesChart({ sessionId, driverId }: LapTimesChartProps) {
  const font = useFont(TitilliumWeb_400Regular, 10)
  const { data, isLoading } = useDriverLaps(sessionId, driverId)
  const { rows, compounds } = useMemo(() => buildChartRows(data?.items ?? []), [data])

  if (!driverId || !sessionId) {
    return (
      <View className="h-64 items-center justify-center">
        <Text className="text-sm text-muted">No active session to show lap times for.</Text>
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
        accessibilityLabel={`Lap time by tyre age, one line per compound: ${compounds.join(", ")}`}
      >
        <CartesianChart
          data={rows}
          xKey="tyreAge"
          yKeys={[...COMPOUND_KEYS]}
          domainPadding={{ top: 12, bottom: 12, left: 8, right: 8 }}
          axisOptions={{
            font,
            labelColor: AXIS_LABEL_COLOR,
            lineColor: AXIS_LINE_COLOR,
            formatXLabel: (value) => String(Math.round(value)),
            formatYLabel: (value) => formatLapTime(value),
          }}
        >
          {({ points }) => (
            <>
              {compounds.map((key) => (
                <Line
                  key={`line-${key}`}
                  points={points[key]}
                  color={getCompoundColor(key)}
                  strokeWidth={2}
                  connectMissingData
                />
              ))}
              {compounds.map((key) => (
                <Scatter
                  key={`dots-${key}`}
                  points={points[key]}
                  color={getCompoundColor(key)}
                  radius={3}
                />
              ))}
            </>
          )}
        </CartesianChart>
      </View>
      <Text className="mt-1 text-center text-xs text-muted">Tyre age (laps)</Text>
      <View className="mt-2 flex-row flex-wrap items-center justify-center gap-4">
        {compounds.map((key) => (
          <LegendSwatch key={key} color={getCompoundColor(key)} label={key} />
        ))}
      </View>
    </View>
  )
}
