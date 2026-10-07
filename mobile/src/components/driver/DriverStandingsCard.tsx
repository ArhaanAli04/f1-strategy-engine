import { router } from "expo-router"
import { useMemo } from "react"
import { Pressable, ScrollView, Text, View } from "react-native"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { useDriverStandings } from "@/hooks/useDriverStandings"
import { useDrivers } from "@/hooks/useDrivers"
import { FALLBACK_TEAM_COLOR, ROUTES } from "@/utils/constants"
import { displayDriverName } from "@/utils/driverNames"
import { ERGAST_DRIVER_IDS } from "@/utils/ergastDriverIds"
import { rowLogoSize } from "@/utils/rowLogoSizes"
import type { DriverResponse } from "@/types"

// Derived from the wall clock, as the roster's constructor standings are.
const CURRENT_YEAR = new Date().getFullYear()
// About seven rows; the rest scroll inside the card.
const STANDINGS_LIST_HEIGHT_PX = 300
const LOGO_BOX_WIDTH_PX = 40
const ROW_BORDER_COLOR = "rgba(255, 255, 255, 0.1)"

// Ergast driverId -> our driver code, for a standing whose Driver has no
// code field.
const CODE_BY_ERGAST_ID: Record<string, string> = Object.fromEntries(
  Object.entries(ERGAST_DRIVER_IDS).map(([code, ergastId]) => [ergastId, code]),
)

interface StandingRow {
  key: string
  position: string
  name: string
  points: string
  driver: DriverResponse | undefined
}

// The current season's drivers' championship (mobile only, Drivers tab,
// 2026-10-07): position, team logo, name and points, from one Ergast request.
// A fixed-height card whose rows scroll inside it, so the roster stays right
// below. Each standing is matched to our roster by driver code for its logo
// and driver page; one we don't have (a replacement driver) keeps Ergast's
// name and a team-colour square, and doesn't open anything.
export function DriverStandingsCard() {
  const { data, isLoading, isError } = useDriverStandings(CURRENT_YEAR)
  const { data: drivers } = useDrivers()

  const rows = useMemo<StandingRow[]>(() => {
    const driversByCode = new Map((drivers ?? []).map((driver) => [driver.code, driver]))
    return (data?.standings ?? []).map((standing) => {
      const code = standing.Driver.code ?? CODE_BY_ERGAST_ID[standing.Driver.driverId]
      const driver = code ? driversByCode.get(code) : undefined
      return {
        key: standing.Driver.driverId,
        position: standing.position,
        name: driver
          ? displayDriverName(driver.full_name, driver.code)
          : displayDriverName(`${standing.Driver.givenName} ${standing.Driver.familyName}`, code),
        points: standing.points,
        driver,
      }
    })
  }, [data, drivers])

  const subtitle = data?.round ? `after Round ${data.round}` : `${CURRENT_YEAR} season`

  return (
    <View className="overflow-hidden rounded-md border border-white/10 bg-surface">
      <View className="flex-row items-baseline justify-between px-3 py-2">
        <Text className="text-sm font-semibold text-foreground">Drivers&apos; Championship</Text>
        <Text className="text-xs text-muted">{subtitle}</Text>
      </View>
      <View className="flex-row items-center border-b border-white/10 px-3 py-1">
        <Text className="w-8 text-center text-[10px] font-medium text-muted">POS</Text>
        <Text className="ml-2 flex-1 text-[10px] font-medium text-muted">DRIVER</Text>
        <Text className="text-right text-[10px] font-medium text-muted">PTS</Text>
      </View>
      {isLoading ? (
        <View style={{ height: STANDINGS_LIST_HEIGHT_PX }} />
      ) : isError || rows.length === 0 ? (
        <View className="items-center justify-center p-6">
          <Text className="text-center text-sm text-muted">Standings not available yet.</Text>
        </View>
      ) : (
        <ScrollView style={{ height: STANDINGS_LIST_HEIGHT_PX }} nestedScrollEnabled>
          {rows.map((row) => {
            const team = row.driver?.contracts[0]?.team
            const driverId = row.driver?.id
            return (
              <Pressable
                key={row.key}
                onPress={driverId ? () => router.push(ROUTES.DRIVER_DETAIL(driverId)) : undefined}
                disabled={!driverId}
                accessibilityRole={driverId ? "button" : undefined}
                accessibilityLabel={`${row.position}, ${row.name}, ${row.points} points`}
                style={{ borderBottomWidth: 1, borderBottomColor: ROW_BORDER_COLOR }}
                className="flex-row items-center px-3 py-2 active:opacity-70"
              >
                <Text className="w-8 text-center font-mono text-xs text-muted">{row.position}</Text>
                <View style={{ width: LOGO_BOX_WIDTH_PX }} className="ml-2 h-6 items-center justify-center">
                  <TeamLogo
                    teamName={team?.name}
                    teamColor={team?.color_hex ?? FALLBACK_TEAM_COLOR}
                    size={rowLogoSize(team?.name)}
                  />
                </View>
                <Text numberOfLines={1} className="ml-2 flex-1 text-sm font-semibold text-foreground">
                  {row.name}
                </Text>
                <Text className="font-mono text-sm text-foreground">{row.points}</Text>
              </Pressable>
            )
          })}
        </ScrollView>
      )}
    </View>
  )
}
