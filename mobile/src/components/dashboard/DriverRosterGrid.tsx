import { router } from "expo-router"
import { useMemo } from "react"
import { Pressable, Text, View } from "react-native"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { useRosterDrivers } from "@/hooks/useRosterDrivers"
import { FALLBACK_TEAM_COLOR, ROUTES } from "@/utils/constants"

function chunkPairs<T>(items: T[]): T[][] {
  const pairs: T[][] = []
  for (let i = 0; i < items.length; i += 2) pairs.push(items.slice(i, i + 2))
  return pairs
}

// RN port of web/src/components/dashboard/DriverRosterGrid.tsx for the Home
// tab, added 2026-10-06 so Home matches web's and desktop's dashboards. In
// constructor-standings order, like the Drivers tab (useRosterDrivers); the
// cards look like the Drivers tab's and open Driver Detail. Plain
// two-column rows rather than a FlatList, because Home is a ScrollView and a
// nested FlatList scrolling the same way would warn and lose virtualisation
// anyway (22 drivers).
export function DriverRosterGrid() {
  const { rosterDrivers, isLoading } = useRosterDrivers()
  const rows = useMemo(() => chunkPairs(rosterDrivers), [rosterDrivers])

  if (isLoading) {
    return <View className="h-40 w-full rounded-md bg-surface" />
  }

  if (rows.length === 0) {
    return <Text className="text-sm text-muted">No drivers available.</Text>
  }

  return (
    <View className="gap-2">
      {rows.map((pair) => (
        <View key={pair[0].id} className="flex-row gap-2">
          {pair.map((driver) => {
            const team = driver.contracts[0]?.team
            return (
              <Pressable
                key={driver.id}
                onPress={() => router.push(ROUTES.DRIVER_DETAIL(driver.id))}
                className="flex-1 flex-row items-center gap-2 overflow-hidden rounded-md border border-white/10 bg-surface p-2 active:opacity-70"
              >
                <TeamLogo teamName={team?.name} teamColor={team?.color_hex ?? FALLBACK_TEAM_COLOR} />
                <View className="min-w-0 flex-1">
                  <Text numberOfLines={1} className="text-sm font-semibold text-foreground">
                    {driver.code}
                  </Text>
                  <Text numberOfLines={1} className="text-xs text-muted">
                    {team?.name ?? "No team"}
                  </Text>
                </View>
              </Pressable>
            )
          })}
          {pair.length === 1 && <View className="flex-1" />}
        </View>
      ))}
    </View>
  )
}
