import { useMemo } from "react"
import { useQueries } from "@tanstack/react-query"
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import type { LapDataResponse } from "@/types"

interface SectorComparisonProps {
  sessionId: string | null
  driverId: string | null
}

type SectorKey = "sector1_seconds" | "sector2_seconds" | "sector3_seconds"

const SECTORS: { key: SectorKey; label: string }[] = [
  { key: "sector1_seconds", label: "S1" },
  { key: "sector2_seconds", label: "S2" },
  { key: "sector3_seconds", label: "S3" },
]

function meanOf(values: number[]): number | null {
  if (values.length === 0) return null
  return values.reduce((sum, v) => sum + v, 0) / values.length
}

// Mirrors driver_service.py's _performance_vs_team_avg convention: each
// teammate's own mean is computed first (only valid laps with a time set),
// then those per-driver means are averaged with equal weight per driver —
// not a flat mean over every lap, which would let whichever driver ran more
// laps this session dominate the "team average".
function perDriverSectorMean(laps: LapDataResponse[], key: SectorKey): number | null {
  const values = laps
    .filter((lap) => lap.is_valid && lap[key] !== null)
    .map((lap) => lap[key] as number)
  return meanOf(values)
}

export function SectorComparison({ sessionId, driverId }: SectorComparisonProps) {
  const { data: drivers } = useDrivers()

  const driver = drivers?.find((d) => d.id === driverId) ?? null
  const teamId = driver?.contracts[0]?.team_id ?? null
  const teammateIds = useMemo(
    () => (drivers ?? []).filter((d) => d.contracts[0]?.team_id === teamId).map((d) => d.id),
    [drivers, teamId],
  )

  const lapsQueries = useQueries({
    queries: teammateIds.map((id) => driverLapsQueryOptions(sessionId, id)),
  })

  const isLoading = Boolean(driverId) && (!drivers || lapsQueries.some((q) => q.isLoading))

  if (!driverId || !sessionId) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
        No active session to compare sectors against.
      </div>
    )
  }

  if (isLoading) {
    return <LoadingSkeleton className="h-64 w-full" />
  }

  if (!teamId || teammateIds.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
        No team data available for this driver.
      </div>
    )
  }

  const perDriverMeans = teammateIds.map((id, index) => {
    const laps = lapsQueries[index]?.data?.items ?? []
    return {
      driverId: id,
      means: Object.fromEntries(
        SECTORS.map(({ key }) => [key, perDriverSectorMean(laps, key)]),
      ) as Record<SectorKey, number | null>,
    }
  })

  const driverMeans = perDriverMeans.find((entry) => entry.driverId === driverId)?.means

  const chartData = SECTORS.map(({ key, label }) => {
    const teamValues = perDriverMeans
      .map((entry) => entry.means[key])
      .filter((v): v is number => v !== null)
    return {
      sector: label,
      driver: driverMeans?.[key] ?? null,
      teamAvg: meanOf(teamValues),
    }
  })

  return (
    <div
      role="img"
      aria-label={`Sector times for ${driver?.code ?? "driver"} versus team average: ${chartData
        .map((entry) => `${entry.sector} driver ${entry.driver?.toFixed(2) ?? "—"}, team ${entry.teamAvg?.toFixed(2) ?? "—"}`)
        .join("; ")}`}
    >
      <ResponsiveContainer width="100%" height={256}>
        <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis dataKey="sector" className="text-xs fill-muted-foreground" />
          <YAxis
            domain={["auto", "auto"]}
            tickFormatter={(value: number) => value.toFixed(1)}
            className="text-xs fill-muted-foreground"
          />
          <Tooltip formatter={(value) => (typeof value === "number" ? value.toFixed(3) : "—")} />
          <Legend />
          <Bar dataKey="driver" name={driver?.code ?? "Driver"} fill="var(--primary)" isAnimationActive={false} />
          <Bar dataKey="teamAvg" name="Team Avg" fill="var(--muted-foreground)" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
