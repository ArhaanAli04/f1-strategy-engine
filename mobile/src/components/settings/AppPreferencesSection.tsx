import { Switch, Text, View } from "react-native"
import { usePreferencesStore } from "@/stores/preferencesStore"

// Mobile only: web and desktop have no haptics. Saved on the phone
// (preferencesStore), so it applies at once and needs no Save button, unlike
// the account sections beside it.
export function AppPreferencesSection() {
  const hapticsEnabled = usePreferencesStore((state) => state.hapticsEnabled)
  const setHapticsEnabled = usePreferencesStore((state) => state.setHapticsEnabled)

  return (
    <View className="gap-2">
      <Text className="text-base font-semibold text-foreground">App</Text>
      <View className="flex-row items-center justify-between gap-2 rounded-md border border-white/10 px-3 py-2.5">
        <View className="flex-1 gap-1.5">
          <Text className="text-sm text-foreground">Haptic feedback</Text>
          <Text className="text-xs text-muted">
            Vibrations when you pick an option, run a simulation or get a result. Saved on this phone.
          </Text>
        </View>
        <Switch
          value={hapticsEnabled}
          onValueChange={setHapticsEnabled}
          accessibilityLabel="Haptic feedback"
        />
      </View>
    </View>
  )
}
