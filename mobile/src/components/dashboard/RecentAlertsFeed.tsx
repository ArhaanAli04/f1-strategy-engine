import { Ionicons } from "@expo/vector-icons"
import { useQuery } from "@tanstack/react-query"
import { router } from "expo-router"
import { Pressable, Text, View } from "react-native"
import * as alertsApi from "@/api/alerts"
import { DriverChip } from "@/components/shared/DriverChip"
import { ROUTES } from "@/utils/constants"
import { AlertType } from "@/types"

const RECENT_ALERTS_COUNT = 3

// Same icon mapping as the Alerts tab — kept as a local copy, same as web's
// two independent copies (DashboardPage/AlertsPage have no other coupling).
const ALERT_ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  [AlertType.UNDERCUT_THREAT]: "trending-down-outline",
  [AlertType.PIT_WINDOW_OPEN]: "timer-outline",
  [AlertType.SAFETY_CAR_PROBABILITY]: "rainy-outline",
  [AlertType.FASTEST_LAP_THREAT]: "flag-outline",
  [AlertType.COMPETITOR_PITTED]: "people-outline",
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
  })
}

// RN port of web/src/components/dashboard/RecentAlertsFeed.tsx — spec says
// "last 3" for the mobile Home screen (web shows 5).
export function RecentAlertsFeed() {
  const { data, isLoading } = useQuery({
    queryKey: ["alerts", "list"],
    queryFn: () => alertsApi.getAlerts(),
  })

  const recentAlerts = (data ?? []).slice(0, RECENT_ALERTS_COUNT)

  return (
    <View className="gap-2 rounded-lg border border-white/10 bg-surface p-3">
      <View className="flex-row items-center justify-between">
        <Text className="text-base font-semibold text-foreground">Recent Alerts</Text>
        <Pressable onPress={() => router.push(ROUTES.ALERTS)}>
          <Text className="text-xs font-medium text-foreground underline">View all</Text>
        </Pressable>
      </View>
      {isLoading && <Text className="text-sm text-muted">Loading…</Text>}
      {!isLoading && recentAlerts.length === 0 && (
        <Text className="text-sm text-muted">No alerts yet.</Text>
      )}
      {recentAlerts.map((alert) => {
        const iconName = ALERT_ICONS[alert.alert_type] ?? "flag-outline"
        return (
          <View key={alert.id} className="flex-row items-start gap-3 rounded-md border border-white/10 p-2.5">
            <Ionicons name={iconName} size={16} color="#999999" style={{ marginTop: 2 }} />
            <View className="flex-1 gap-1">
              <View className="flex-row items-center gap-2">
                {alert.driver_id && <DriverChip driverId={alert.driver_id} />}
                <Text className="text-xs text-muted">{formatTimestamp(alert.triggered_at)}</Text>
              </View>
              <Text className="text-sm text-foreground">{alert.message}</Text>
            </View>
          </View>
        )
      })}
    </View>
  )
}
