import { Text, View } from "react-native"
import { useDrivers } from "@/hooks/useDrivers"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"

interface DriverChipProps {
  driverId: string
}

// RN port of web/src/components/shared/DriverChip.tsx — same driverId ->
// code/team-color resolution via the shared useDrivers() cache.
export function DriverChip({ driverId }: DriverChipProps) {
  const { data: drivers } = useDrivers()
  const driver = drivers?.find((d) => d.id === driverId)
  const teamColor = driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR

  return (
    <View className="flex-row items-center gap-1.5 self-start rounded-full border border-white/10 px-2 py-0.5">
      <View className="h-2 w-2 rounded-full" style={{ backgroundColor: teamColor }} />
      <Text className="text-xs font-semibold text-foreground">{driver?.code ?? "???"}</Text>
    </View>
  )
}
