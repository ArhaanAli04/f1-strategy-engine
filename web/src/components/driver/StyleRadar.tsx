import { useQuery } from "@tanstack/react-query"
import {
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts"
import * as driverApi from "@/api/driver"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { CHART_TOOLTIP_STYLE } from "@/utils/constants"

interface StyleRadarProps {
  driverId: string | null
  sessionId: string | null
}

// Axes are exactly the 4 real fields backend/schemas/driver_schema.py's
// DriverAnalysisResponse returns (see services/driver_service.py's
// _fit_population) — there is no aggression/smoothness/qualifying_pace field
// on the backend, and no UMAP scatter is rendered here (see CLAUDE.md's
// Deferred Telemetry Features: braking/throttle-derived style features were
// never ingested, so driver_style.py ships these 4 lap/stint-level proxies
// instead of the original spec's metrics).
const RADAR_AXES: {
  key: "sector_time_variance" | "tyre_management_index" | "lap_time_consistency" | "stint_length_tendency"
  label: string
}[] = [
  { key: "sector_time_variance", label: "Sector Variance" },
  { key: "tyre_management_index", label: "Tyre Management" },
  { key: "lap_time_consistency", label: "Consistency" },
  { key: "stint_length_tendency", label: "Stint Length" },
]

export function StyleRadar({ driverId, sessionId }: StyleRadarProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["driver", "analysis", driverId, sessionId],
    queryFn: () => driverApi.getDriverAnalysis(driverId as string, sessionId as string),
    enabled: Boolean(driverId && sessionId),
    retry: false,
  })

  if (!driverId || !sessionId) {
    return (
      <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
        No active session to derive a style profile from.
      </div>
    )
  }

  if (isLoading) {
    return <LoadingSkeleton className="h-72 w-full" />
  }

  if (!data) {
    return (
      <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
        No style profile available for this driver.
      </div>
    )
  }

  const chartData = RADAR_AXES.map(({ key, label }) => ({
    metric: label,
    value: data[key],
  }))

  return (
    <div
      role="img"
      aria-label={`Driving style radar for archetype ${data.archetype}: ${chartData
        .map((point) => `${point.metric} ${point.value.toFixed(2)}`)
        .join(", ")}`}
    >
      <p className="mb-2 text-xs text-muted-foreground">
        Archetype: <span className="font-semibold text-foreground">{data.archetype}</span>
      </p>
      <ResponsiveContainer width="100%" height={288}>
        <RadarChart data={chartData} outerRadius="75%">
          <PolarGrid className="stroke-border" />
          <PolarAngleAxis dataKey="metric" className="text-xs fill-muted-foreground" />
          <Tooltip
            formatter={(value) => (typeof value === "number" ? value.toFixed(3) : value)}
            {...CHART_TOOLTIP_STYLE}
          />
          <Radar
            dataKey="value"
            stroke="var(--primary)"
            fill="var(--primary)"
            fillOpacity={0.35}
            isAnimationActive={false}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}
