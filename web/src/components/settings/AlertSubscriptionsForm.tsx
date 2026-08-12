import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bell, Check } from "lucide-react"
import { toast } from "sonner"
import * as alertsApi from "@/api/alerts"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { useDrivers } from "@/hooks/useDrivers"
import { useSavedFlash } from "@/hooks/useSavedFlash"
import { isActiveDriver } from "@/utils/drivers"
import { getApiErrorMessage } from "@/utils/errors"
import { AlertType, type DriverResponse, type TeamResponse } from "@/types"

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

function setMembership<T>(set: Set<T>, values: T[], included: boolean): Set<T> {
  const next = new Set(set)
  for (const value of values) {
    if (included) next.add(value)
    else next.delete(value)
  }
  return next
}

interface TeamGroup {
  team: TeamResponse
  drivers: DriverResponse[]
}

// Alphabetical by team name — simplest stable order, no dependency on the
// Ergast constructor-standings fetch DriverRosterGrid.tsx uses (this list
// is about picking who to watch, not ranking teams).
function groupByTeam(drivers: DriverResponse[]): TeamGroup[] {
  const groups = new Map<string, TeamGroup>()
  for (const driver of drivers) {
    const team = driver.contracts[0]?.team
    if (!team) continue
    const existing = groups.get(team.id)
    if (existing) {
      existing.drivers.push(driver)
    } else {
      groups.set(team.id, { team, drivers: [driver] })
    }
  }
  return [...groups.values()].sort((a, b) => a.team.name.localeCompare(b.team.name))
}

interface TeamDriverGroupProps {
  group: TeamGroup
  selectedIds: Set<string>
  onToggleDriver: (driverId: string) => void
  onToggleTeam: (driverIds: string[], nextChecked: boolean) => void
}

function TeamDriverGroup({ group, selectedIds, onToggleDriver, onToggleTeam }: TeamDriverGroupProps) {
  const driverIds = group.drivers.map((driver) => driver.id)
  const selectedCount = driverIds.filter((id) => selectedIds.has(id)).length
  const allSelected = selectedCount === driverIds.length
  const someSelected = selectedCount > 0 && !allSelected
  const checkedState: boolean | "indeterminate" = allSelected ? true : someSelected ? "indeterminate" : false

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Checkbox
          checked={checkedState}
          onCheckedChange={() => onToggleTeam(driverIds, !allSelected)}
          aria-label={`Select all ${group.team.name} drivers`}
        />
        <TeamLogo teamName={group.team.name} teamColor={group.team.color_hex} className="h-5 w-5" />
        <span className="text-sm font-semibold">{group.team.name}</span>
      </div>
      <div className="ml-6 flex flex-wrap gap-1.5">
        {group.drivers.map((driver) => (
          <Label
            key={driver.id}
            className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1.5 text-sm font-normal hover:bg-accent"
          >
            <Checkbox checked={selectedIds.has(driver.id)} onCheckedChange={() => onToggleDriver(driver.id)} />
            {driver.code}
          </Label>
        ))}
      </div>
    </div>
  )
}

export function AlertSubscriptionsForm() {
  const queryClient = useQueryClient()
  const { data: drivers, isLoading: driversLoading } = useDrivers()
  const activeDrivers = (drivers ?? []).filter(isActiveDriver)
  const teamGroups = groupByTeam(activeDrivers)
  const { data: subscription, isLoading: subscriptionLoading } = useQuery({
    queryKey: SUBSCRIPTIONS_QUERY_KEY,
    queryFn: () => alertsApi.getSubscriptions(),
  })

  const [driverIds, setDriverIds] = useState<Set<string>>(new Set())
  const [alertTypes, setAlertTypes] = useState<Set<string>>(new Set())
  const [justSaved, flashSaved] = useSavedFlash()

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
      flashSaved()
    },
    onError: (error) => {
      toast.error(getApiErrorMessage(error, "Failed to save subscriptions"))
    },
  })

  const isLoading = driversLoading || subscriptionLoading

  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <Bell className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">Notifications</h2>
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        Choose which drivers and alert types trigger notifications.
      </p>
      {isLoading ? (
        <LoadingSkeleton className="h-48 w-full" />
      ) : (
        <div className="space-y-6">
          <div className="space-y-3">
            <h3 className="text-sm font-semibold">Alert types</h3>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {Object.values(AlertType).map((type) => (
                <div key={type} className="flex items-center justify-between rounded-md border px-3 py-2">
                  <Label htmlFor={`alert-type-${type}`} className="cursor-pointer text-sm font-normal">
                    {ALERT_TYPE_LABELS[type] ?? type}
                  </Label>
                  <Switch
                    id={`alert-type-${type}`}
                    checked={alertTypes.has(type)}
                    onCheckedChange={() => setAlertTypes((prev) => toggleInSet(prev, type))}
                  />
                </div>
              ))}
            </div>
          </div>

          <Separator />

          <div className="space-y-3">
            <h3 className="text-sm font-semibold">Drivers</h3>
            <div className="grid max-h-80 grid-cols-1 gap-x-4 gap-y-4 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">
              {teamGroups.map((group) => (
                <TeamDriverGroup
                  key={group.team.id}
                  group={group}
                  selectedIds={driverIds}
                  onToggleDriver={(driverId) => setDriverIds((prev) => toggleInSet(prev, driverId))}
                  onToggleTeam={(ids, nextChecked) =>
                    setDriverIds((prev) => setMembership(prev, ids, nextChecked))
                  }
                />
              ))}
            </div>
          </div>

          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? (
              "Saving..."
            ) : justSaved ? (
              <span className="flex items-center gap-1.5">
                <Check className="h-4 w-4" />
                Saved
              </span>
            ) : (
              "Save subscriptions"
            )}
          </Button>
        </div>
      )}
    </div>
  )
}
