import { useQueries } from "@tanstack/react-query"
import { router } from "expo-router"
import { useMemo } from "react"
import { FlatList, Pressable, RefreshControl, Text, View } from "react-native"
import { CircuitMapPanel } from "@/components/circuit/CircuitMapPanel"
import { TyreIcon } from "@/components/telemetry/TyreIcon"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { ROUTES, FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { formatLapTime } from "@/utils/formatters"
import type { DriverGap, DriverResponse, LapDataResponse } from "@/types"

interface TimingRow {
  driverId: string
  position: number
  code: string
  teamColor: string
  lastLapSeconds: number | null
  gapLabel: string
  compound: string | null
}

// formatGap (utils/formatters.ts) is flat-seconds ("+2.345s") — right for
// small sub-lap deltas elsewhere, but a cumulative gap to the leader can
// exceed a minute, so this uses formatLapTime's mm:ss.sss rollover instead.
function formatGapToLeader(seconds: number): string {
  return `+${formatLapTime(seconds)}`
}

// Mirrors web/src/components/telemetry/LiveTimingTower.tsx's
// computeGapLabels exactly — position 1 shows "Leader", a broken
// ahead-chain (null gap) shows "—" for itself and everything behind it.
function computeGapLabels(gaps: DriverGap[]): Record<string, string> {
  const sorted = [...gaps].sort((a, b) => a.position - b.position)
  const labels: Record<string, string> = {}
  let cumulative = 0
  let chainBroken = false

  for (const gap of sorted) {
    if (gap.position === 1) {
      labels[gap.driver_id] = "Leader"
      continue
    }
    if (gap.gap_to_ahead_seconds === null || chainBroken) {
      chainBroken = true
      labels[gap.driver_id] = "—"
      continue
    }
    cumulative += gap.gap_to_ahead_seconds
    labels[gap.driver_id] = formatGapToLeader(cumulative)
  }

  return labels
}

// RN port of web/src/components/telemetry/LiveTimingTower.tsx as a full
// screen (FlatList instead of a fixed-height div, pull-to-refresh instead
// of always-on WS+poll, no FLIP reorder animation — that's DOM
// getBoundingClientRect-based and doesn't have a direct RN equivalent;
// FlatList just re-renders rows in their new order without an animated
// glide between old/new positions).
export default function LiveScreen() {
  const { sessionId } = useResolvedSession()
  const { data: drivers } = useDrivers()
  const { data: gapsResponse, isLoading: gapsLoading, refetch, isRefetching } = useSessionGaps(sessionId)
  const { lapsByDriver } = useLiveTelemetry(sessionId)

  const gaps = useMemo(() => gapsResponse?.gaps ?? [], [gapsResponse])
  const driverIds = useMemo(() => gaps.map((gap) => gap.driver_id), [gaps])

  // REST fallback for compound/lap time before the WS has delivered a live
  // event for this driver yet — same combo as web's LiveTimingTower.
  const lapsQueries = useQueries({
    queries: driverIds.map((driverId) => driverLapsQueryOptions(sessionId, driverId)),
  })

  const driversById = useMemo(() => {
    const map = new Map<string, DriverResponse>()
    for (const driver of drivers ?? []) map.set(driver.id, driver)
    return map
  }, [drivers])

  const latestLapByDriver = useMemo(() => {
    const map = new Map<string, LapDataResponse>()
    driverIds.forEach((driverId, index) => {
      const items = lapsQueries[index]?.data?.items ?? []
      if (items.length === 0) return
      const latest = items.reduce((a, b) => (a.lap_number > b.lap_number ? a : b))
      map.set(driverId, latest)
    })
    return map
    // lapsQueries is a fresh array each render (useQueries) — driverIds is
    // the real change signal, lapsQueries is read for its current .data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driverIds, lapsQueries])

  const gapLabels = useMemo(() => computeGapLabels(gaps), [gaps])

  const rows: TimingRow[] = useMemo(() => {
    return [...gaps]
      .sort((a, b) => a.position - b.position)
      .map((gap) => {
        const driver = driversById.get(gap.driver_id)
        const liveLap = lapsByDriver[gap.driver_id]
        const latestRestLap = latestLapByDriver.get(gap.driver_id)
        return {
          driverId: gap.driver_id,
          position: gap.position,
          code: driver?.code ?? "???",
          teamColor: driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR,
          lastLapSeconds: liveLap?.lap_time_seconds ?? latestRestLap?.lap_time_seconds ?? null,
          gapLabel: gapLabels[gap.driver_id] ?? "—",
          compound: liveLap?.compound ?? latestRestLap?.compound ?? null,
        }
      })
  }, [gaps, driversById, lapsByDriver, latestLapByDriver, gapLabels])

  // CircuitMapPanel renders above the timing rows in every state (loading/
  // empty/populated) — mirrors web's RacePage, where CircuitMapPanel and
  // LiveTimingTower are always both mounted regardless of each other's
  // individual loading/empty states. It resolves its own live/non-race/
  // finished/unknown mode independently via useUpcomingRace/useDriverPositions,
  // so it doesn't need sessionId's gaps-derived loading state to gate it.
  const header = sessionId ? <CircuitMapPanel sessionId={sessionId} /> : null

  if (gapsLoading && rows.length === 0) {
    return (
      <View className="flex-1 bg-background">
        {header}
      </View>
    )
  }

  if (!gapsLoading && rows.length === 0) {
    return (
      <View className="flex-1 bg-background">
        {header}
        <View className="flex-1 items-center justify-center gap-1 p-6">
          <Text className="text-sm font-medium text-foreground">No live race session active</Text>
          <Text className="text-center text-xs text-muted">
            Timing data will appear here during a live race
          </Text>
        </View>
      </View>
    )
  }

  return (
    <FlatList
      className="flex-1 bg-background"
      data={rows}
      keyExtractor={(row) => row.driverId}
      ListHeaderComponent={header}
      refreshControl={
        <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor="#fafafa" />
      }
      renderItem={({ item: row }) => (
        <Pressable
          onPress={() => router.push(ROUTES.DRIVER_DETAIL(row.driverId))}
          className="flex-row items-center justify-between border-b border-white/10 px-3 py-2.5 active:bg-surface"
        >
          <Text className="w-6 text-center font-mono text-xs text-muted">{row.position}</Text>
          <View className="h-5 w-1 rounded-full" style={{ backgroundColor: row.teamColor }} />
          <Text className="w-12 text-sm font-semibold text-foreground">{row.code}</Text>
          <Text className="w-16 text-xs text-muted">{formatLapTime(row.lastLapSeconds)}</Text>
          <Text className="w-20 text-right font-mono text-xs text-muted">{row.gapLabel}</Text>
          <TyreIcon compound={row.compound} />
        </Pressable>
      )}
    />
  )
}
