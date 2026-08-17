import { router } from "expo-router"
import { FlatList, Pressable, RefreshControl, Text, View } from "react-native"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useStrategyOverview } from "@/hooks/useStrategy"
import { ROUTES } from "@/utils/constants"

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

export default function StrategyScreen() {
  const { sessionId } = useResolvedSession()
  const { data: overview, dataUpdatedAt, isLoading, refetch, isRefetching } = useStrategyOverview(sessionId)

  const drivers = overview?.drivers ?? []

  if (isLoading) {
    return (
      <View className="flex-1 bg-background">
        <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
        <RunSimulatorButton />
      </View>
    )
  }

  if (drivers.length === 0) {
    return (
      <View className="flex-1 bg-background">
        <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
        <RunSimulatorButton />
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
      ListHeaderComponent={
        <>
          <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
          <RunSimulatorButton />
        </>
      }
      refreshControl={
        <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor="#fafafa" />
      }
      renderItem={({ item: entry }) => (
        <View className="flex-1">
          <PitWindowCard sessionId={sessionId} driverId={entry.driver_id} compact />
        </View>
      )}
    />
  )
}
