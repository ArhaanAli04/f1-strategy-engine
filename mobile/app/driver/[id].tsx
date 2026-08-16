import { useLocalSearchParams } from "expo-router"
import { useMemo } from "react"
import { ScrollView, Text, View } from "react-native"
import { TyreIcon } from "@/components/telemetry/TyreIcon"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { formatLapTime } from "@/utils/formatters"

interface StatTileProps {
  value: string | number
  label: string
}

function StatTile({ value, label }: StatTileProps) {
  return (
    <View className="items-center gap-0.5">
      <Text className="text-lg font-bold text-foreground">{value}</Text>
      <Text className="text-[10px] uppercase tracking-wide text-muted">{label}</Text>
    </View>
  )
}

// Minimal stub, not a full port of web/src/pages/DriverPage.tsx — per Day 31
// scope, this shows identity + current-session snapshot only (no
// LapTimesChart/SectorComparison/StyleRadar, all of which need charts —
// deferred until victory-native + @shopify/react-native-skia are installed,
// see CLAUDE.md's Day 32 deferred-wiring note).
export default function DriverDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const { data: drivers, isLoading } = useDrivers()
  const { sessionId } = useResolvedSession()
  const { data: gapsResponse } = useSessionGaps(sessionId)
  const { lapsByDriver } = useLiveTelemetry(sessionId)
  const { data: recentLaps } = useDriverLaps(sessionId, id ?? null)

  const driver = drivers?.find((d) => d.id === id) ?? null
  const team = driver?.contracts[0]?.team ?? null
  const teamColor = team?.color_hex ?? FALLBACK_TEAM_COLOR

  const gap = useMemo(
    () => gapsResponse?.gaps.find((g) => g.driver_id === id) ?? null,
    [gapsResponse, id],
  )

  const latestRestLap = useMemo(() => {
    const items = recentLaps?.items ?? []
    if (items.length === 0) return null
    return items.reduce((a, b) => (a.lap_number > b.lap_number ? a : b))
  }, [recentLaps])

  const liveLap = id ? lapsByDriver[id] : undefined
  const lastLapSeconds = liveLap?.lap_time_seconds ?? latestRestLap?.lap_time_seconds ?? null
  const compound = liveLap?.compound ?? latestRestLap?.compound ?? null

  if (isLoading) {
    return <View className="flex-1 bg-background" />
  }

  if (!driver) {
    return (
      <View className="flex-1 items-center justify-center bg-background p-6">
        <Text className="text-sm text-muted">Driver not found.</Text>
      </View>
    )
  }

  return (
    <ScrollView className="flex-1 bg-background">
      <View className="h-1.5" style={{ backgroundColor: teamColor }} />
      <View className="gap-4 p-4">
        <View>
          <Text className="text-2xl font-bold text-foreground">{driver.full_name}</Text>
          <Text className="text-sm text-muted">{team?.name ?? "No team"}</Text>
        </View>

        <View className="flex-row items-center justify-around rounded-lg border border-white/10 bg-surface p-4">
          <StatTile value={gap?.position ?? "—"} label="Position" />
          <StatTile value={formatLapTime(lastLapSeconds)} label="Last lap" />
          <StatTile
            value={gap?.gap_to_ahead_seconds != null ? `+${gap.gap_to_ahead_seconds.toFixed(3)}s` : "—"}
            label="Gap ahead"
          />
          <View className="items-center gap-0.5">
            <TyreIcon compound={compound} />
            <Text className="text-[10px] uppercase tracking-wide text-muted">Tyre</Text>
          </View>
        </View>
      </View>
    </ScrollView>
  )
}
