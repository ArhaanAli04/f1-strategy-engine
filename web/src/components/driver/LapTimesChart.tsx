import { useMemo } from "react"
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { formatLapTime, getCompoundColor } from "@/utils/formatters"
import type { LapDataResponse } from "@/types"

interface LapTimesChartProps {
  sessionId: string | null
  driverId: string | null
}

interface CompoundSeries {
  compound: string
  points: { tyre_age_laps: number; lap_time_seconds: number | null }[]
}

// One line per compound — grouped by compound across the whole session (not
// by contiguous stint, unlike telemetry/LapTimeChart.tsx) and plotted
// against tyre age so multiple stints on the same compound overlay into one
// comparable degradation curve. Actual lap times only — no predicted line,
// since no endpoint returns a predicted-vs-actual pair (see CLAUDE.md).
function buildCompoundSeries(laps: LapDataResponse[]): CompoundSeries[] {
  const byCompound = new Map<string, CompoundSeries["points"]>()
  for (const lap of laps) {
    const points = byCompound.get(lap.compound) ?? []
    points.push({ tyre_age_laps: lap.tyre_age_laps, lap_time_seconds: lap.lap_time_seconds })
    byCompound.set(lap.compound, points)
  }
  return Array.from(byCompound.entries()).map(([compound, points]) => ({
    compound,
    points: [...points].sort((a, b) => a.tyre_age_laps - b.tyre_age_laps),
  }))
}

export function LapTimesChart({ sessionId, driverId }: LapTimesChartProps) {
  const { data, isLoading } = useDriverLaps(sessionId, driverId)
  const laps = useMemo(() => data?.items ?? [], [data])
  const series = useMemo(() => buildCompoundSeries(laps), [laps])

  if (!driverId) {
    return (
      <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
        Select a driver to see their lap times.
      </div>
    )
  }

  if (isLoading) {
    return <LoadingSkeleton className="h-72 w-full" />
  }

  if (laps.length === 0) {
    return (
      <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
        No laps recorded yet for this driver.
      </div>
    )
  }

  return (
    <div
      role="img"
      aria-label={`Lap time by tyre age, one line per compound: ${series
        .map((entry) => entry.compound)
        .join(", ")}`}
    >
      <ResponsiveContainer width="100%" height={288}>
        <LineChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis
            dataKey="tyre_age_laps"
            type="number"
            domain={["dataMin", "dataMax"]}
            allowDecimals={false}
            label={{ value: "Tyre age (laps)", position: "insideBottom", offset: -4 }}
            className="text-xs fill-muted-foreground"
          />
          <YAxis
            dataKey="lap_time_seconds"
            domain={["auto", "auto"]}
            tickFormatter={(value: number) => formatLapTime(value)}
            label={{ value: "Lap time", angle: -90, position: "insideLeft" }}
            className="text-xs fill-muted-foreground"
          />
          <Tooltip
            formatter={(value) => formatLapTime(typeof value === "number" ? value : null)}
            labelFormatter={(label) => `Tyre age ${label}`}
          />
          <Legend />
          {series.map((entry) => (
            <Line
              key={entry.compound}
              data={entry.points}
              dataKey="lap_time_seconds"
              name={entry.compound}
              stroke={getCompoundColor(entry.compound)}
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
