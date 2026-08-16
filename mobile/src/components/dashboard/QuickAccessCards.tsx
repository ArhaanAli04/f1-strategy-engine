import { Ionicons } from "@expo/vector-icons"
import { router } from "expo-router"
import { Pressable, Text, View } from "react-native"
import { ROUTES } from "@/utils/constants"

interface QuickAccessCardProps {
  icon: keyof typeof Ionicons.glyphMap
  label: string
  description: string
  onPress: () => void
}

function QuickAccessCard({ icon, label, description, onPress }: QuickAccessCardProps) {
  return (
    <Pressable
      onPress={onPress}
      className="flex-1 gap-2 rounded-lg border border-white/10 bg-surface p-4 active:opacity-70"
    >
      <Ionicons name={icon} size={20} color="#fafafa" />
      <Text className="text-sm font-semibold text-foreground">{label}</Text>
      <Text className="text-xs text-muted">{description}</Text>
    </Pressable>
  )
}

// RN port of web/src/components/dashboard/QuickAccessCards.tsx. Web's third
// card ("Driver Analytics") scroll-anchors to an #driver-roster section on
// the same page — mobile has no roster section on Home, so it navigates
// straight to the Drivers tab instead.
export function QuickAccessCards() {
  return (
    <View className="flex-row gap-3">
      <QuickAccessCard
        icon="radio-outline"
        label="Live Race"
        description="Timing tower & strategy"
        onPress={() => router.push(ROUTES.LIVE)}
      />
      <QuickAccessCard
        icon="people-outline"
        label="Drivers"
        description="Full 2026 roster"
        onPress={() => router.push(ROUTES.DRIVERS)}
      />
    </View>
  )
}
