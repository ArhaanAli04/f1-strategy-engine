import { Ionicons } from "@expo/vector-icons"
import { useQueries } from "@tanstack/react-query"
import { router } from "expo-router"
import { useMemo, useState } from "react"
import { Pressable, RefreshControl, ScrollView, Text, View } from "react-native"
import { CircuitMapPanel } from "@/components/circuit/CircuitMapPanel"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { LapTimeChart } from "@/components/telemetry/LapTimeChart"
import { SectorHeatmap } from "@/components/telemetry/SectorHeatmap"
import { TyreIcon } from "@/components/telemetry/TyreIcon"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useSessionStore } from "@/stores/sessionStore"
import { ROUTES, FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { displayDriverName } from "@/utils/driverNames"
import { formatLapTime } from "@/utils/formatters"
import { rowLogoSize } from "@/utils/rowLogoSizes"
import * as haptics from "@/utils/haptics"
import type { DriverGap, DriverResponse, LapDataResponse } from "@/types"

interface TimingRow {
  driverId: string
  position: number
  displayName: string
  teamName: string | undefined
  teamColor: string
  gapLabel: string
  compound: string | null
}

// The tower shows each team's logo in place of web's colour bar (mobile only,
// owner's choice 2026-10-06), sized by rowLogoSize (utils/rowLogoSizes.ts).

// Space between the GAP and TYRE columns, set as a style rather than a class:
// NativeWind never applied the ml-4/ml-6 classes tried first (not used
// anywhere else in the app), and on the phone the two columns touched.
const GAP_TO_TYRE_SPACING_PX = 16

type LiveView = "timing" | "laps" | "sectors"

const LIVE_VIEWS: { key: LiveView; label: string }[] = [
  { key: "timing", label: "Timing" },
  { key: "laps", label: "Lap Times" },
  { key: "sectors", label: "Sectors" },
]

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

interface ViewSwitchProps {
  view: LiveView
  onChange: (view: LiveView) => void
}

function ViewSwitch({ view, onChange }: ViewSwitchProps) {
  return (
    <View className="bg-background px-3 py-2">
      <View className="flex-row rounded-md border border-white/10 bg-surface">
        {LIVE_VIEWS.map(({ key, label }) => {
          const active = view === key
          return (
            <Pressable
              key={key}
              onPress={() => onChange(key)}
              accessibilityRole="tab"
              accessibilityState={{ selected: active }}
              className={`flex-1 items-center border-b-2 py-2.5 ${active ? "border-foreground" : "border-transparent"}`}
            >
              <Text className={`text-xs font-medium ${active ? "text-foreground" : "text-muted"}`}>
                {label}
              </Text>
            </Pressable>
          )
        })}
      </View>
    </View>
  )
}

// RN port of web's race page for a phone (owner's layout, 2026-10-06): the
// circuit map, then a Timing | Lap Times | Sectors switch that sticks to the
// top once the map scrolls away, then the chosen view. Web shows the tower,
// LapTimeChart and SectorHeatmap side by side; a phone shows one at a time.
// Tapping a tower row selects the driver (sessionStore, as on web), which the
// map, Lap Times and Sectors all follow; the row's › opens Driver Detail.
// A row is position, team logo, driver name, gap and tyre: no last lap time (the
// Sectors view has it, coloured), mobile only since 2026-10-06.
// The tower has no FLIP reorder animation (web's is DOM-measurement based):
// rows simply re-render in their new order. Pull to refresh refetches the
// gaps.
export default function LiveScreen() {
  const { sessionId } = useResolvedSession()
  const { data: drivers } = useDrivers()
  const {
    data: gapsResponse,
    dataUpdatedAt,
    isLoading: gapsLoading,
    refetch,
    isRefetching,
  } = useSessionGaps(sessionId)
  // The tab's one WebSocket: LapTimeChart and SectorHeatmap get its events as
  // a prop, because each useLiveTelemetry call would open another.
  const { lapsByDriver } = useLiveTelemetry(sessionId)
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const setSelectedDriver = useSessionStore((state) => state.setSelectedDriver)
  const [view, setView] = useState<LiveView>("timing")

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
          displayName: displayDriverName(driver?.full_name, driver?.code),
          teamName: driver?.contracts[0]?.team?.name,
          teamColor: driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR,
          gapLabel: gapLabels[gap.driver_id] ?? "—",
          compound: liveLap?.compound ?? latestRestLap?.compound ?? null,
        }
      })
  }, [gaps, driversById, lapsByDriver, latestLapByDriver, gapLabels])

  const selectedCode = selectedDriverId ? driversById.get(selectedDriverId)?.code : undefined

  function renderTiming() {
    if (gapsLoading && rows.length === 0) {
      return <View className="mx-3 h-64 rounded-md bg-surface" />
    }
    if (rows.length === 0) {
      return (
        <View className="items-center gap-1 p-6">
          <Text className="text-sm font-medium text-foreground">No live race session active</Text>
          <Text className="text-center text-xs text-muted">
            Timing data will appear here during a live race
          </Text>
        </View>
      )
    }
    // Column labels styled like the Sectors table's header. Widths match the
    // row cells below; DRIVER spans the logo and name, and the last spacer
    // is the › button's width.
    const header = (
      <View key="header" className="flex-row items-center border-b border-white/10 py-1 pl-3 pr-1">
        <Text numberOfLines={1} className="w-8 text-center text-[10px] font-medium text-muted">POS</Text>
        <Text className="ml-2 flex-1 text-center text-[10px] font-medium text-muted">DRIVER</Text>
        <Text className="w-20 text-right text-[10px] font-medium text-muted">GAP</Text>
        <Text
          style={{ marginLeft: GAP_TO_TYRE_SPACING_PX }}
          className="w-9 text-center text-[10px] font-medium text-muted"
        >
          TYRE
        </Text>
        <View className="w-8" />
      </View>
    )
    return [header, ...rows.map((row) => {
      const isSelected = row.driverId === selectedDriverId
      return (
        <Pressable
          key={row.driverId}
          onPress={() => {
            if (row.driverId !== selectedDriverId) haptics.selectionTick()
            setSelectedDriver(row.driverId)
          }}
          accessibilityRole="button"
          accessibilityState={{ selected: isSelected }}
          accessibilityLabel={`Select ${row.displayName}`}
          className={`flex-row items-center border-b border-white/10 py-2.5 pl-3 pr-1 ${isSelected ? "bg-pill" : "active:bg-surface"}`}
        >
          <Text className="w-8 text-center font-mono text-xs text-muted">{row.position}</Text>
          <View className="ml-2 h-6 w-10 items-center justify-center">
            <TeamLogo
              teamName={row.teamName}
              teamColor={row.teamColor}
              size={rowLogoSize(row.teamName)}
            />
          </View>
          {/* Long names (Gabriel BORTOLETO) shrink a little to fit on a
              narrow phone before they would be cut off. */}
          <Text
            numberOfLines={1}
            adjustsFontSizeToFit
            minimumFontScale={0.8}
            className="ml-2 flex-1 text-sm font-semibold text-foreground"
          >
            {row.displayName}
          </Text>
          <Text className="w-20 text-right font-mono text-xs text-muted">{row.gapLabel}</Text>
          <View style={{ marginLeft: GAP_TO_TYRE_SPACING_PX }} className="w-9 items-center">
            <TyreIcon compound={row.compound} />
          </View>
          <Pressable
            onPress={() => router.push(ROUTES.DRIVER_DETAIL(row.driverId))}
            hitSlop={10}
            accessibilityRole="button"
            accessibilityLabel={`Open ${row.displayName}'s driver page`}
            className="px-2 py-1"
          >
            <Ionicons name="chevron-forward" size={16} color="#9ca3af" />
          </Pressable>
        </Pressable>
      )
    })]
  }

  function renderView() {
    if (view === "timing") return renderTiming()
    if (!sessionId) {
      return (
        <View className="items-center p-6">
          <Text className="text-sm text-muted">No race session to show.</Text>
        </View>
      )
    }
    if (view === "laps") {
      return (
        <View className="mx-3 rounded-md border border-white/10 bg-surface p-4">
          <Text className="mb-3 text-sm font-semibold text-foreground">
            Lap Times{selectedCode ? ` — ${selectedCode}` : ""}
          </Text>
          <LapTimeChart sessionId={sessionId} driverId={selectedDriverId} lapsByDriver={lapsByDriver} />
        </View>
      )
    }
    return (
      <View className="mx-3 rounded-md border border-white/10 bg-surface py-3">
        <Text className="mb-2 px-3 text-sm font-semibold text-foreground">Sector Times</Text>
        <SectorHeatmap sessionId={sessionId} lapsByDriver={lapsByDriver} />
      </View>
    )
  }

  // The map is shown in every state, as on web's race page, where it and the
  // tower load independently. stickyHeaderIndices counts children, so the
  // switch's index depends on whether the map is there.
  const switchIndex = sessionId ? 1 : 0

  return (
    <View className="flex-1 bg-background">
      <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
      <ScrollView
        className="flex-1"
        stickyHeaderIndices={[switchIndex]}
        contentContainerClassName="pb-6"
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor="#fafafa" />
        }
      >
        {sessionId ? <CircuitMapPanel sessionId={sessionId} /> : null}
        <ViewSwitch
          view={view}
          onChange={(next) => {
            if (next !== view) haptics.selectionTick()
            setView(next)
          }}
        />
        <View>{renderView()}</View>
      </ScrollView>
    </View>
  )
}
