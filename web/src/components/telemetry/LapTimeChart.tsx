import { useMemo } from "react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { formatLapTime, getCompoundColor } from "@/utils/formatters"
import type { LapDataResponse } from "@/types"

interface LapTimeChartProps {
  sessionId: string | null
  driverId: string | null
}

interface LapPoint {
  lap_number: number
  lap_time_seconds: number | null
}

interface CompoundSegment {
  compound: string
  points: LapPoint[]
}

// Recharts has no native "recolor this line where the underlying series
// value changes" primitive — the standard workaround is one <Line> per
// compound-contiguous run, each given its own `data` (Recharts supports a
// per-series `data` override so multiple Lines can share one XAxis/YAxis
// with different-length series). Each new segment repeats its predecessor's
// last point so the drawn line stays visually continuous across the
// compound change instead of leaving a gap.
function buildCompoundSegments(laps: LapDataResponse[]): CompoundSegment[] {
  const sorted = [...laps].sort((a, b) => a.lap_number - b.lap_number)
  const segments: CompoundSegment[] = []

  for (const lap of sorted) {
    const point: LapPoint = { lap_number: lap.lap_number, lap_time_seconds: lap.lap_time_seconds }
    const current = segments[segments.length - 1]
    if (current && current.compound === lap.compound) {
      current.points.push(point)
    } else {
      const bridge = current ? [current.points[current.points.length - 1]] : []
      segments.push({ compound: lap.compound, points: [...bridge, point] })
    }
  }

  return segments
}

function findCompoundChangeLaps(laps: LapDataResponse[]): number[] {
  const sorted = [...laps].sort((a, b) => a.lap_number - b.lap_number)
  const changeLaps: number[] = []
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].compound !== sorted[i - 1].compound) changeLaps.push(sorted[i].lap_number)
  }
  return changeLaps
}

export function LapTimeChart({ sessionId, driverId }: LapTimeChartProps) {
  const { data, isLoading } = useDriverLaps(sessionId, driverId)
  const laps = useMemo(() => data?.items ?? [], [data])

  const segments = useMemo(() => buildCompoundSegments(laps), [laps])
  const changeLaps = useMemo(() => findCompoundChangeLaps(laps), [laps])

  if (!driverId) {
    return (
      <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
        Select a driver in the timing tower to see their lap times.
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
    <ResponsiveContainer width="100%" height={288}>
      <LineChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis
          dataKey="lap_number"
          type="number"
          domain={["dataMin", "dataMax"]}
          allowDecimals={false}
          label={{ value: "Lap", position: "insideBottom", offset: -4 }}
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
          labelFormatter={(label) => `Lap ${label}`}
        />
        {changeLaps.map((lapNumber) => (
          <ReferenceLine
            key={lapNumber}
            x={lapNumber}
            stroke="var(--muted-foreground)"
            strokeDasharray="4 4"
            label={{ value: "Pit", position: "top", fontSize: 10 }}
          />
        ))}
        {segments.map((segment, index) => (
          <Line
            key={`${segment.compound}-${index}`}
            data={segment.points}
            dataKey="lap_time_seconds"
            stroke={getCompoundColor(segment.compound)}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls
            isAnimationActive={false}
            name={segment.compound}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
