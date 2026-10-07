import { Ionicons } from "@expo/vector-icons"
import { router } from "expo-router"
import { Pressable, Text, View } from "react-native"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { UndercutThreatPanel } from "@/components/strategy/UndercutThreatPanel"
import { useDrivers } from "@/hooks/useDrivers"
import { FALLBACK_TEAM_COLOR, ROUTES } from "@/utils/constants"
import { displayDriverName } from "@/utils/driverNames"
import * as haptics from "@/utils/haptics"

interface DriverStrategySheetProps {
  sessionId: string | null
  driverId: string | null
}

// The Strategy tab's driver detail (mobile only, 2026-10-07): the driver's
// full pit-window card, the undercut panel (car ahead and car behind), and a
// button that opens the Simulator with this session and driver filled in.
// Shown by app/strategy-driver.tsx, a native iOS form sheet sized to fit this
// content (sheetAllowedDetents "fitToContents"), so it is a plain View, not a
// ScrollView: a scroll view inside a fit-to-contents sheet doesn't size it.
export function DriverStrategySheet({ sessionId, driverId }: DriverStrategySheetProps) {
  const { data: drivers } = useDrivers()
  const driver = drivers?.find((d) => d.id === driverId)
  const team = driver?.contracts[0]?.team

  function openSimulator() {
    if (!sessionId || !driverId) return
    haptics.confirm()
    router.dismiss()
    router.push({ pathname: ROUTES.SIMULATOR, params: { sessionId, driverId } })
  }

  return (
    <View className="bg-background">
      <View className="flex-row items-center gap-3 border-b border-white/10 px-4 py-3">
        <TeamLogo teamName={team?.name} teamColor={team?.color_hex ?? FALLBACK_TEAM_COLOR} size={28} />
        <View className="flex-1">
          <Text numberOfLines={1} className="text-base font-semibold text-foreground">
            {driver ? displayDriverName(driver.full_name, driver.code) : "Driver"}
          </Text>
          <Text numberOfLines={1} className="text-xs text-muted">
            {team?.name ?? "No team"}
          </Text>
        </View>
        <Pressable onPress={() => router.dismiss()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
          <Ionicons name="close" size={22} color="#fafafa" />
        </Pressable>
      </View>
      <View className="gap-3 p-4">
        <PitWindowCard sessionId={sessionId} driverId={driverId} />
        <UndercutThreatPanel sessionId={sessionId} driverId={driverId} />
        <Pressable
          onPress={openSimulator}
          disabled={!sessionId || !driverId}
          accessibilityRole="button"
          className="flex-row items-center justify-center gap-2 rounded-md bg-foreground py-3 active:opacity-70"
        >
          <Ionicons name="flask-outline" size={16} color="#0a0a0a" />
          <Text className="text-sm font-semibold text-background">Simulate this driver</Text>
        </Pressable>
      </View>
    </View>
  )
}
