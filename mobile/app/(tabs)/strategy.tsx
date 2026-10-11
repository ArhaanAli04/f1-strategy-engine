import { router } from "expo-router"
import { FlatList, Pressable, RefreshControl, Text, View } from "react-native"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { useRaceSession } from "@/hooks/useRaceSession"
import { useSharedLiveTelemetry } from "@/hooks/useSharedLiveTelemetry"
import { useStrategyOverview } from "@/hooks/useStrategy"
import { useSessionStore } from "@/stores/sessionStore"
import { ROUTES } from "@/utils/constants"
import * as haptics from "@/utils/haptics"

// The selected card's outline; the others keep a transparent border of the
// same width so selecting doesn't shift the grid.
const SELECTED_BORDER_COLOR = "rgba(250, 250, 250, 0.7)"
const CARD_BORDER_WIDTH_PX = 2
const CARD_RADIUS_PX = 8

function formatRaceDate(isoDate: string): string {
  return new Date(isoDate).toLocaleDateString(undefined, { day: "numeric", month: "short" })
}

// RN port of web/src/components/strategy/StrategyOverviewGrid.tsx as a full
// screen — FlatList with numColumns={2} instead of a CSS grid, pull-to-
// refresh instead of always-on polling. "Run Simulator" button added Day 32
// Checkpoint 4 — the Simulator screen (app/simulator.tsx) is reached from
// here rather than a 6th tab, keeping the tab bar at 5 (Home/Live/Strategy/
// Drivers/Alerts), per the Day 32 spec's explicit choice.
function RunSimulatorButton() {
  return (
    <Pressable
      onPress={() => router.push(ROUTES.SIMULATOR)}
      className="mx-3 mt-3 items-center rounded-md border border-white/10 bg-surface py-3"
    >
      <Text className="text-sm font-semibold text-foreground">Run Simulator</Text>
    </Pressable>
  )
}

// The tab's heading (mobile only, 2026-10-07): "Strategy Dashboard", then
// which race the predictions are for and whether it is live. The line about
// tapping a card is the list's footer, below the last card (owner's choice:
// less text above the cards). During a Demo Replay it says so, with the
// replay's lap (Day 6b-mobile CP4, 2026-10-09).
function DashboardHeader() {
  const { sessionId, isLive, isReplay, raceName, raceDate } = useRaceSession()
  const { lapsByDriver } = useSharedLiveTelemetry(sessionId)
  if (!sessionId) return null
  // The furthest-along car's lap, from the shared live connection's events.
  const laps = Object.values(lapsByDriver).map((event) => event.lap_number)
  const replayLap = laps.length > 0 ? Math.max(...laps) : null
  // Short on purpose (owner, 2026-10-07) so it fits on the title's line:
  // "Belgian GP · 26 Jul", "· Live now" during a live race, or
  // "· Demo Replay · Lap 18" during a replay.
  const race = (raceName ?? "Race").replace("Grand Prix", "GP")
  const status = isReplay
    ? `Demo Replay${replayLap !== null ? ` · Lap ${replayLap}` : ""}`
    : isLive
      ? "Live now"
      : raceDate
        ? formatRaceDate(raceDate)
        : null
  return (
    <View className="mx-3 mt-3 flex-row items-baseline justify-between gap-2">
      <Text className="text-lg font-semibold text-foreground">Strategy Dashboard</Text>
      <Text numberOfLines={1} className="flex-1 text-right text-xs text-muted">
        {status ? `${race} · ${status}` : race}
      </Text>
    </View>
  )
}

// The strategy wall, one compact pit-window card per driver (since
// 2026-10-07, mobile only): tapping a card selects that driver and opens
// their sheet (full pit window, undercut panel, simulate). The
// selection is sessionStore's selectedDriverId, shared with the Live tab, so
// a driver picked there is outlined here and the other way round. The sheet
// is the app/strategy-driver.tsx route, a native form sheet sized to its
// content.
export default function StrategyScreen() {
  const { sessionId } = useRaceSession()
  const { data: overview, dataUpdatedAt, isLoading, refetch, isRefetching } = useStrategyOverview(sessionId)
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const setSelectedDriver = useSessionStore((state) => state.setSelectedDriver)

  const drivers = overview?.drivers ?? []

  function openDriver(driverId: string) {
    haptics.selectionTick()
    setSelectedDriver(driverId)
    if (sessionId) router.push({ pathname: ROUTES.STRATEGY_DRIVER, params: { sessionId, driverId } })
  }

  const header = (
    <>
      <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
      <RunSimulatorButton />
      <DashboardHeader />
    </>
  )

  if (isLoading) {
    return <View className="flex-1 bg-background">{header}</View>
  }

  if (drivers.length === 0) {
    return (
      <View className="flex-1 bg-background">
        {header}
        <View className="flex-1 items-center justify-center gap-1 p-6">
          <Text className="text-sm font-medium text-foreground">No live race session active</Text>
          <Text className="text-center text-xs text-muted">
            Strategy predictions will appear here during a live race
          </Text>
        </View>
      </View>
    )
  }

  return (
      <FlatList
        className="flex-1 bg-background"
        contentContainerClassName="gap-2 p-3"
        columnWrapperClassName="gap-2"
        data={drivers}
        numColumns={2}
        keyExtractor={(entry) => entry.driver_id}
        extraData={selectedDriverId}
        ListHeaderComponent={header}
        ListFooterComponent={
          <Text className="mt-2 text-center text-xs text-muted">
            Tap a driver&apos;s card to see their pit window and undercut threats, and to simulate their
            strategy.
          </Text>
        }
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor="#fafafa" />
        }
        renderItem={({ item: entry }) => {
          const selected = entry.driver_id === selectedDriverId
          return (
            <Pressable
              onPress={() => openDriver(entry.driver_id)}
              accessibilityRole="button"
              accessibilityState={{ selected }}
              accessibilityHint="Opens this driver's pit window and undercut threats"
              className="flex-1 active:opacity-70"
              style={{
                borderWidth: CARD_BORDER_WIDTH_PX,
                borderRadius: CARD_RADIUS_PX,
                borderColor: selected ? SELECTED_BORDER_COLOR : "transparent",
              }}
            >
              <PitWindowCard sessionId={sessionId} driverId={entry.driver_id} compact />
            </Pressable>
          )
        }}
      />
  )
}
