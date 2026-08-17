import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Alert, Pressable, Switch, Text, View } from "react-native"
import * as alertsApi from "@/api/alerts"
import { useDrivers } from "@/hooks/useDrivers"
import { isActiveDriver } from "@/utils/drivers"
import { getApiErrorMessage } from "@/utils/errors"
import { AlertType } from "@/types"

const SUBSCRIPTIONS_QUERY_KEY = ["alerts", "subscriptions"] as const

const ALERT_TYPE_LABELS: Record<string, string> = {
  [AlertType.UNDERCUT_THREAT]: "Undercut threat",
  [AlertType.PIT_WINDOW_OPEN]: "Pit window open",
  [AlertType.SAFETY_CAR_PROBABILITY]: "Safety car probability",
  [AlertType.FASTEST_LAP_THREAT]: "Fastest lap threat",
  [AlertType.COMPETITOR_PITTED]: "Competitor pitted",
}

function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

// Simplified vs. web's AlertSubscriptionsForm: a flat alphabetical driver
// list rather than team-grouped chips with per-team select-all — the
// grouping polish is deferred. Same underlying read/save contract
// (GET/PUT /alerts/subscriptions), so nothing downstream needs updating
// when the grouped version lands.
export function AlertSubscriptionsSection() {
  const queryClient = useQueryClient()
  const { data: drivers, isLoading: driversLoading } = useDrivers()
  const activeDrivers = (drivers ?? [])
    .filter(isActiveDriver)
    .sort((a, b) => a.code.localeCompare(b.code))
  const { data: subscription, isLoading: subscriptionLoading } = useQuery({
    queryKey: SUBSCRIPTIONS_QUERY_KEY,
    queryFn: () => alertsApi.getSubscriptions(),
  })

  const [driverIds, setDriverIds] = useState<Set<string>>(new Set())
  const [alertTypes, setAlertTypes] = useState<Set<string>>(new Set())

  // team_ids has no UI here either — carried through unchanged on save.
  useEffect(() => {
    if (!subscription) return
    setDriverIds(new Set(subscription.driver_ids))
    setAlertTypes(new Set(subscription.alert_types))
  }, [subscription])

  const saveMutation = useMutation({
    mutationFn: () =>
      alertsApi.updateSubscriptions({
        driver_ids: [...driverIds],
        team_ids: subscription?.team_ids ?? [],
        alert_types: [...alertTypes],
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(SUBSCRIPTIONS_QUERY_KEY, updated)
      Alert.alert("Saved", "Alert subscriptions updated.")
    },
    onError: (error) => {
      Alert.alert("Error", getApiErrorMessage(error, "Failed to save subscriptions"))
    },
  })

  const isLoading = driversLoading || subscriptionLoading

  if (isLoading) {
    return <Text className="text-sm text-muted">Loading…</Text>
  }

  return (
    <View className="gap-6">
      <View className="gap-2">
        <Text className="text-base font-semibold text-foreground">Alert types</Text>
        {Object.values(AlertType).map((type) => (
          <View
            key={type}
            className="flex-row items-center justify-between rounded-md border border-white/10 px-3 py-2.5"
          >
            <Text className="text-sm text-foreground">{ALERT_TYPE_LABELS[type] ?? type}</Text>
            <Switch
              value={alertTypes.has(type)}
              onValueChange={() => setAlertTypes((prev) => toggleInSet(prev, type))}
            />
          </View>
        ))}
      </View>

      <View className="gap-2">
        <Text className="text-base font-semibold text-foreground">Drivers</Text>
        <View className="flex-row flex-wrap gap-2">
          {activeDrivers.map((driver) => {
            const selected = driverIds.has(driver.id)
            return (
              <Pressable
                key={driver.id}
                onPress={() => setDriverIds((prev) => toggleInSet(prev, driver.id))}
                className={`rounded-full border px-3 py-1.5 ${
                  selected ? "border-foreground bg-foreground" : "border-white/10 bg-surface"
                }`}
              >
                <Text className={`text-xs font-medium ${selected ? "text-background" : "text-foreground"}`}>
                  {driver.code}
                </Text>
              </Pressable>
            )
          })}
        </View>
      </View>

      <Pressable
        onPress={() => saveMutation.mutate()}
        disabled={saveMutation.isPending}
        className="items-center rounded-md bg-foreground py-3 disabled:opacity-50"
      >
        <Text className="text-base font-semibold text-background">
          {saveMutation.isPending ? "Saving..." : "Save subscriptions"}
        </Text>
      </Pressable>
    </View>
  )
}
