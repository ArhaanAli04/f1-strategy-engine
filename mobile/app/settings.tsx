import { useState } from "react"
import { Pressable, ScrollView, Text, View } from "react-native"
import { SafeAreaView } from "react-native-safe-area-context"
import { AlertSubscriptionsSection } from "@/components/settings/AlertSubscriptionsSection"
import { PasswordSection } from "@/components/settings/PasswordSection"
import { ProfileSection } from "@/components/settings/ProfileSection"

type Section = "profile" | "password" | "notifications"

const SECTIONS: { key: Section; label: string }[] = [
  { key: "profile", label: "Profile" },
  { key: "password", label: "Password" },
  { key: "notifications", label: "Notifications" },
]

// Web's SettingsModal uses a sidebar of three sections inside a Dialog —
// mobile uses a horizontal pill row instead (a sidebar doesn't fit a phone
// width), same three sections and same underlying data/mutations.
export default function SettingsScreen() {
  const [section, setSection] = useState<Section>("profile")

  return (
    <SafeAreaView className="flex-1 bg-background" edges={["bottom"]}>
      <View className="flex-row gap-2 border-b border-white/10 px-4 py-3">
        {SECTIONS.map(({ key, label }) => (
          <Pressable
            key={key}
            onPress={() => setSection(key)}
            className={`rounded-full px-3 py-1.5 ${section === key ? "bg-foreground" : "bg-surface"}`}
          >
            <Text className={`text-sm font-medium ${section === key ? "text-background" : "text-muted"}`}>
              {label}
            </Text>
          </Pressable>
        ))}
      </View>
      <ScrollView contentContainerClassName="p-4" keyboardShouldPersistTaps="handled">
        {section === "profile" && <ProfileSection />}
        {section === "password" && <PasswordSection />}
        {section === "notifications" && <AlertSubscriptionsSection />}
      </ScrollView>
    </SafeAreaView>
  )
}
