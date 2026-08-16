import { FlatList, RefreshControl, Text, View } from "react-native"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useStrategyOverview } from "@/hooks/useStrategy"

// RN port of web/src/components/strategy/StrategyOverviewGrid.tsx as a full
// screen — FlatList with numColumns={2} instead of a CSS grid, pull-to-
// refresh instead of always-on polling.
export default function StrategyScreen() {
  const { sessionId } = useResolvedSession()
  const { data: overview, isLoading, refetch, isRefetching } = useStrategyOverview(sessionId)

  const drivers = overview?.drivers ?? []

  if (isLoading) {
    return <View className="flex-1 bg-background" />
  }

  if (drivers.length === 0) {
    return (
      <View className="flex-1 items-center justify-center gap-1 bg-background p-6">
        <Text className="text-sm font-medium text-foreground">No live race session active</Text>
        <Text className="text-center text-xs text-muted">
          Strategy predictions will appear here during a live race
        </Text>
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
