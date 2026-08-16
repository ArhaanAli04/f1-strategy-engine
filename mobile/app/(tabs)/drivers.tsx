import { router } from "expo-router"
import { useMemo } from "react"
import { FlatList, Pressable, Text, View } from "react-native"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { useDrivers } from "@/hooks/useDrivers"
import { FALLBACK_TEAM_COLOR, ROUTES } from "@/utils/constants"
import { isActiveDriver } from "@/utils/drivers"
import type { DriverResponse } from "@/types"

// RN port of web/src/components/dashboard/DriverRosterGrid.tsx as a full
// screen — FlatList with numColumns instead of a CSS grid. Simplified vs.
// web: sorted alphabetically by team name rather than by real Ergast
// constructor-standings position (useConstructorStandings wasn't ported —
// it's an extra external API integration not needed for anything else on
// mobile today). Same fallback web itself uses when standings are
// unavailable, just not the primary sort here.
function sortByTeamName(drivers: DriverResponse[]): DriverResponse[] {
  return [...drivers].sort((a, b) => {
    const teamA = a.contracts[0]?.team?.name ?? ""
    const teamB = b.contracts[0]?.team?.name ?? ""
    return teamA.localeCompare(teamB)
  })
}

export default function DriversScreen() {
  const { data: drivers, isLoading } = useDrivers()

  const activeDrivers = useMemo(
    () => sortByTeamName((drivers ?? []).filter(isActiveDriver)),
    [drivers],
  )

  if (isLoading) {
    return <View className="flex-1 bg-background" />
  }

  return (
    <FlatList
      className="flex-1 bg-background"
      contentContainerClassName="gap-2 p-3"
      columnWrapperClassName="gap-2"
      data={activeDrivers}
      numColumns={2}
      keyExtractor={(driver) => driver.id}
      renderItem={({ item: driver }) => {
        const team = driver.contracts[0]?.team
        return (
          <Pressable
            onPress={() => router.push(ROUTES.DRIVER_DETAIL(driver.id))}
            className="flex-1 flex-row items-center gap-2 overflow-hidden rounded-md border border-white/10 bg-surface p-2 active:opacity-70"
          >
            <TeamLogo teamColor={team?.color_hex ?? FALLBACK_TEAM_COLOR} />
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
