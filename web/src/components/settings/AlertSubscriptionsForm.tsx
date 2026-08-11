import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import * as alertsApi from "@/api/alerts"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { useDrivers } from "@/hooks/useDrivers"
import { getApiErrorMessage } from "@/utils/errors"
import { AlertType, type DriverResponse } from "@/types"

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
  if (next.has(value)) {
    next.delete(value)
  } else {
    next.add(value)
  }
  return next
}

// GET /drivers returns every driver ever ingested, including retired/
// historical ones with no current-season contract — same filter as
// DriverRosterGrid.tsx, so only active-roster drivers are subscribable.
function isActiveDriver(driver: DriverResponse): boolean {
  const contract = driver.contracts[0]
  return Boolean(contract?.team_id) && Boolean(contract?.team?.name)
}

export function AlertSubscriptionsForm() {
  const queryClient = useQueryClient()
  const { data: drivers, isLoading: driversLoading } = useDrivers()
  const activeDrivers = (drivers ?? []).filter(isActiveDriver)
  const { data: subscription, isLoading: subscriptionLoading } = useQuery({
    queryKey: SUBSCRIPTIONS_QUERY_KEY,
    queryFn: () => alertsApi.getSubscriptions(),
  })

  const [driverIds, setDriverIds] = useState<Set<string>>(new Set())
  const [alertTypes, setAlertTypes] = useState<Set<string>>(new Set())

  // team_ids has no UI here — carried through unchanged on save (see below)
  // rather than dropped, since SubscriptionCreate requires the field.
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
      toast.success("Alert subscriptions saved")
    },
    onError: (error) => {
      toast.error(getApiErrorMessage(error, "Failed to save subscriptions"))
    },
  })

  const isLoading = driversLoading || subscriptionLoading

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Alert Subscriptions</CardTitle>
        <CardDescription>Choose which drivers and alert types trigger notifications.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {isLoading ? (
          <LoadingSkeleton className="h-48 w-full" />
        ) : (
          <>
            <div>
              <h3 className="mb-2 text-sm font-semibold">Alert types</h3>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {Object.values(AlertType).map((type) => (
                  <Label key={type} className="flex cursor-pointer items-center gap-2 text-sm font-normal">
                    <input
                      type="checkbox"
                      checked={alertTypes.has(type)}
                      onChange={() => setAlertTypes((prev) => toggleInSet(prev, type))}
                      className="h-4 w-4 rounded border-input"
                    />
                    {ALERT_TYPE_LABELS[type] ?? type}
                  </Label>
                ))}
              </div>
            </div>

            <div>
              <h3 className="mb-2 text-sm font-semibold">Drivers</h3>
              <div className="grid max-h-64 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3">
                {activeDrivers.map((driver) => (
                  <Label
                    key={driver.id}
                    className="flex cursor-pointer items-center gap-2 text-sm font-normal"
                  >
                    <input
                      type="checkbox"
                      checked={driverIds.has(driver.id)}
                      onChange={() => setDriverIds((prev) => toggleInSet(prev, driver.id))}
                      className="h-4 w-4 rounded border-input"
                    />
                    {driver.code}
                  </Label>
                ))}
              </div>
            </div>

            <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
              {saveMutation.isPending ? "Saving..." : "Save subscriptions"}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}
