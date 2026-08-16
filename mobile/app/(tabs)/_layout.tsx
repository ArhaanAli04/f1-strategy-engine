import { Ionicons } from "@expo/vector-icons"
import { router, Tabs } from "expo-router"
import { Pressable, Text, View, type OpaqueColorValue } from "react-native"
import { useAlertStore } from "@/stores/alertStore"

function SettingsButton() {
  return (
    <Pressable
      onPress={() => router.push("/settings")}
      hitSlop={8}
      className="mr-4"
      accessibilityLabel="Settings"
    >
      <Ionicons name="settings-outline" size={22} color="#fafafa" />
    </Pressable>
  )
}

// unreadCount is 0 until Checkpoint 4 wires the Alerts tab's real fetch
// into alertStore — the badge itself is fully functional today, it just
// has no data source yet.
// color comes from react-navigation's tabBarIcon callback (ColorValue) —
// typed to match Ionicons' own color prop (string | OpaqueColorValue, its
// declared type, narrower than RN's full ColorValue union) so both ends
// agree without a cast.
function AlertsTabIcon({ color, size }: { color: string | OpaqueColorValue; size: number }) {
  const unreadCount = useAlertStore((state) => state.unreadCount)
  return (
    <View>
      <Ionicons name="notifications-outline" size={size} color={color} />
      {unreadCount > 0 && (
        <View className="absolute -right-1.5 -top-1 h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1">
          <Text className="text-[10px] font-bold text-white">{unreadCount > 9 ? "9+" : unreadCount}</Text>
        </View>
      )}
    </View>
  )
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: true,
        headerStyle: { backgroundColor: "#0a0a0a" },
        headerTintColor: "#fafafa",
        headerRight: () => <SettingsButton />,
        tabBarStyle: { backgroundColor: "#0a0a0a", borderTopColor: "rgba(255,255,255,0.1)" },
        tabBarActiveTintColor: "#ffffff",
        tabBarInactiveTintColor: "#555555",
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Home",
          tabBarIcon: ({ color, size }) => <Ionicons name="home-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="live"
        options={{
          title: "Live",
          tabBarIcon: ({ color, size }) => <Ionicons name="flag-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="strategy"
        options={{
          title: "Strategy",
          tabBarIcon: ({ color, size }) => <Ionicons name="bar-chart-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="drivers"
        options={{
          title: "Drivers",
          tabBarIcon: ({ color, size }) => <Ionicons name="people-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="alerts"
        options={{
          title: "Alerts",
          tabBarIcon: ({ color, size }) => <AlertsTabIcon color={color} size={size} />,
        }}
      />
    </Tabs>
  )
}
