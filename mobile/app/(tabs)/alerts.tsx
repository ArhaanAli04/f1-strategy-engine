import { Ionicons } from "@expo/vector-icons"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { FlatList, Text, View } from "react-native"
import { Swipeable } from "react-native-gesture-handler"
import * as alertsApi from "@/api/alerts"
import { DriverChip } from "@/components/shared/DriverChip"
import { useAlertStore } from "@/stores/alertStore"
import { AlertType, type AlertResponse } from "@/types"

const ALERTS_QUERY_KEY = ["alerts", "list"] as const

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
    second: "2-digit",
    day: "2-digit",
    month: "short",
  })
}

function MarkReadAction() {
  return (
    <View className="w-20 items-center justify-center bg-foreground">
      <Ionicons name="checkmark-outline" size={20} color="#0a0a0a" />
      <Text className="text-[10px] font-semibold text-background">Read</Text>
    </View>
  )
}

interface AlertRowProps {
  alert: AlertResponse
  onMarkRead: (alertId: string) => void
}

// "Dismiss" here mirrors web's AlertRow semantics — swiping marks the alert
// read (isUnread -> false, dimmed style) rather than removing it from the
// list entirely.
function AlertRow({ alert, onMarkRead }: AlertRowProps) {
  const iconName = ALERT_ICONS[alert.alert_type] ?? "flag-outline"
  const isUnread = alert.read_at === null

  const row = (
    <View
      className={`flex-row items-start gap-3 border-b border-white/10 p-3 ${isUnread ? "bg-surface" : "opacity-60"}`}
    >
      <Ionicons name={iconName} size={16} color="#999999" style={{ marginTop: 2 }} />
      <View className="flex-1 gap-1">
        <View className="flex-row items-center gap-2">
          {alert.driver_id && <DriverChip driverId={alert.driver_id} />}
          <Text className="text-xs text-muted">{formatTimestamp(alert.triggered_at)}</Text>
          {isUnread && <View className="h-1.5 w-1.5 rounded-full bg-foreground" />}
        </View>
        <Text className="text-sm text-foreground">{alert.message}</Text>
      </View>
    </View>
  )

  if (!isUnread) return row

  return (
    <Swipeable
      renderRightActions={() => <MarkReadAction />}
      onSwipeableOpen={(direction) => {
        if (direction === "right") onMarkRead(alert.id)
      }}
    >
      {row}
    </Swipeable>
  )
}

// RN port of web/src/pages/AlertsPage.tsx — swipe-to-mark-read (via
// react-native-gesture-handler's Swipeable) instead of web's tap-to-mark-
// read button. Populates alertStore exactly like web (source of truth for
// the Alerts tab's unread badge, wired in Checkpoint 3).
export default function AlertsScreen() {
  const setAlerts = useAlertStore((state) => state.setAlerts)
  const markReadInStore = useAlertStore((state) => state.markRead)
  const alerts = useAlertStore((state) => state.alerts)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ALERTS_QUERY_KEY,
    queryFn: () => alertsApi.getAlerts(),
  })

  useEffect(() => {
    if (data) setAlerts(data)
  }, [data, setAlerts])

  const markReadMutation = useMutation({
    mutationFn: (alertId: string) => alertsApi.markAlertRead(alertId),
    onSuccess: (_updated, alertId) => {
      markReadInStore(alertId)
      queryClient.invalidateQueries({ queryKey: ALERTS_QUERY_KEY })
    },
  })

  if (isLoading) {
    return <View className="flex-1 bg-background" />
  }

  if (alerts.length === 0) {
    return (
      <View className="flex-1 items-center justify-center bg-background p-6">
        <Text className="text-sm text-muted">No alerts yet.</Text>
      </View>
    )
  }

  return (
    <FlatList
      className="flex-1 bg-background"
      data={alerts}
      keyExtractor={(alert) => alert.id}
      renderItem={({ item: alert }) => (
        <AlertRow alert={alert} onMarkRead={markReadMutation.mutate} />
      )}
    />
  )
}
