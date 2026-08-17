import { useMemo } from "react"
import { useQueries } from "@tanstack/react-query"
import { TitilliumWeb_400Regular } from "@expo-google-fonts/titillium-web/400Regular"
import { useFont } from "@shopify/react-native-skia"
import { Text, View } from "react-native"
import { BarGroup, CartesianChart } from "victory-native"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
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

// Mirrors web's per-driver-mean-then-averaged convention (see
// web/src/components/driver/SectorComparison.tsx) — copied verbatim, it's
// pure data transformation with no browser/DOM dependency.
function perDriverSectorMean(laps: LapDataResponse[], key: SectorKey): number | null {
  const values = laps
    .filter((lap) => lap.is_valid && lap[key] !== null)
    .map((lap) => lap[key] as number)
  return meanOf(values)
}

const CHART_HEIGHT = 220
const DRIVER_COLOR = "#60a5fa"
const TEAM_AVG_COLOR = "#6b7280"
const AXIS_LABEL_COLOR = "#9ca3af"
const AXIS_LINE_COLOR = "rgba(255,255,255,0.1)"

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <View className="flex-row items-center gap-1.5">
      <View className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color }} />
      <Text className="text-xs text-muted">{label}</Text>
    </View>
  )
}

// RN port of web/src/components/driver/SectorComparison.tsx. Grouped bars use
// victory-native's real CartesianChart + BarGroup API (confirmed against the
// installed 41.26.0 source, Checkpoint 3) — the classic web `victory`
// package's VictoryBar/VictoryChart naming doesn't apply to this Skia
// rewrite. Axis tick labels need a real Skia font object (useFont), reusing
// the same Titillium Web asset already bundled for RN Text via expo-font —
// Skia's Canvas has its own independent font/text subsystem, so this is a
// second load of the same font file, not shared with expo-font's registration.
export function SectorComparison({ sessionId, driverId }: SectorComparisonProps) {
  const font = useFont(TitilliumWeb_400Regular, 10)
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
      <View className="h-56 items-center justify-center">
        <Text className="text-sm text-muted">No active session to compare sectors against.</Text>
      </View>
    )
  }

  if (isLoading) {
    return <View className="h-56 w-full rounded-md bg-surface" />
  }

  if (!teamId || teammateIds.length === 0) {
    return (
      <View className="h-56 items-center justify-center">
        <Text className="text-sm text-muted">No team data available for this driver.</Text>
      </View>
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

  // BarGroup's Skia paths need numeric y values — null (no valid laps for
  // that sector yet) collapses to 0 rather than an omitted bar, a small
  // simplification vs. web's Recharts bars (which just don't render a null
  // segment) since a real 20-driver session should have laps for all 3
  // sectors long before this screen is reachable.
  const chartData = SECTORS.map(({ key, label }) => {
    const teamValues = perDriverMeans
      .map((entry) => entry.means[key])
      .filter((v): v is number => v !== null)
    return {
      sector: label,
      driver: driverMeans?.[key] ?? 0,
      teamAvg: meanOf(teamValues) ?? 0,
    }
  })

  return (
    <View>
      <View style={{ height: CHART_HEIGHT }}>
        <CartesianChart
          data={chartData}
          xKey="sector"
          yKeys={["driver", "teamAvg"]}
          domainPadding={{ left: 50, right: 50, top: 24 }}
          axisOptions={{ font, labelColor: AXIS_LABEL_COLOR, lineColor: AXIS_LINE_COLOR }}
        >
          {({ points, chartBounds }) => (
            <BarGroup
              chartBounds={chartBounds}
              betweenGroupPadding={0.35}
              withinGroupPadding={0.15}
              roundedCorners={{ topLeft: 4, topRight: 4 }}
            >
              <BarGroup.Bar points={points.driver} color={DRIVER_COLOR} />
              <BarGroup.Bar points={points.teamAvg} color={TEAM_AVG_COLOR} />
            </BarGroup>
          )}
        </CartesianChart>
      </View>
      <View className="mt-2 flex-row items-center justify-center gap-4">
        <LegendSwatch color={DRIVER_COLOR} label={driver?.code ?? "Driver"} />
        <LegendSwatch color={TEAM_AVG_COLOR} label="Team Avg" />
      </View>
    </View>
  )
}
