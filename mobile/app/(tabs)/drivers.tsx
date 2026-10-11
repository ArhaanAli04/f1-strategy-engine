import { router } from "expo-router"
import { FlatList, Pressable, Text, View } from "react-native"
import { DriverStandingsCard } from "@/components/driver/DriverStandingsCard"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { useRosterDrivers } from "@/hooks/useRosterDrivers"
import { FALLBACK_TEAM_COLOR, ROUTES } from "@/utils/constants"

// RN port of web/src/components/dashboard/DriverRosterGrid.tsx as a full
// screen — FlatList with numColumns instead of a CSS grid. Sorted by the
// current constructor standings like web (useRosterDrivers, since
// 2026-10-06; alphabetical by team name before). Above the roster, the
// season's drivers' championship in a card that scrolls on its own
// (DriverStandingsCard, mobile only, 2026-10-07).
export default function DriversScreen() {
  const { rosterDrivers: activeDrivers, dataUpdatedAt, isLoading } = useRosterDrivers()

  if (isLoading) {
    return (
      <View className="flex-1 bg-background">
        <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
      </View>
    )
  }

  return (
    <FlatList
      className="flex-1 bg-background"
      contentContainerClassName="gap-2 p-3"
      columnWrapperClassName="gap-2"
      data={activeDrivers}
      numColumns={2}
      keyExtractor={(driver) => driver.id}
      ListHeaderComponent={
        <View className="gap-3">
          <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
          <DriverStandingsCard />
          <Text className="text-lg font-semibold text-foreground">Driver Roster</Text>
        </View>
      }
      renderItem={({ item: driver }) => {
        const team = driver.contracts[0]?.team
        return (
          <Pressable
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
      }}
    />
  )
}
