import { useEffect } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CloudRain, Flag, Timer, TrendingDown, Users, type LucideIcon } from "lucide-react"
import * as alertsApi from "@/api/alerts"
import { DriverChip } from "@/components/shared/DriverChip"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { cn } from "@/lib/utils"
import { useAlertStore } from "@/stores/alertStore"
import { AlertType, type AlertResponse } from "@/types"

const ALERTS_QUERY_KEY = ["alerts", "list"] as const

const ALERT_ICONS: Record<string, LucideIcon> = {
  [AlertType.UNDERCUT_THREAT]: TrendingDown,
  [AlertType.PIT_WINDOW_OPEN]: Timer,
  [AlertType.SAFETY_CAR_PROBABILITY]: CloudRain,
  [AlertType.FASTEST_LAP_THREAT]: Flag,
  [AlertType.COMPETITOR_PITTED]: Users,
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

interface AlertRowProps {
  alert: AlertResponse
  onMarkRead: (alertId: string) => void
}

function AlertRow({ alert, onMarkRead }: AlertRowProps) {
  const Icon = ALERT_ICONS[alert.alert_type] ?? Flag
  const isUnread = alert.read_at === null

  return (
    <button
      type="button"
      onClick={() => isUnread && onMarkRead(alert.id)}
      className={cn(
        "flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors",
        isUnread ? "bg-accent/50" : "opacity-70 hover:opacity-100",
      )}
    >
      <Icon className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
      <div className="flex-1 space-y-1">
        <div className="flex items-center gap-2">
          {alert.driver_id && <DriverChip driverId={alert.driver_id} />}
          <span className="text-xs text-muted-foreground">{formatTimestamp(alert.triggered_at)}</span>
          {isUnread && <span className="h-1.5 w-1.5 rounded-full bg-primary" />}
        </div>
        <p className="text-sm">{alert.message}</p>
      </div>
    </button>
  )
}

export function AlertsPage() {
  const setAlerts = useAlertStore((state) => state.setAlerts)
  const markReadInStore = useAlertStore((state) => state.markRead)
  const alerts = useAlertStore((state) => state.alerts)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ALERTS_QUERY_KEY,
    queryFn: () => alertsApi.getAlerts(),
  })

  // Populates alertStore (source of truth for NavBar's unread badge) once
  // the feed loads — the store itself never fetches on its own.
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
    return (
      <div className="mx-auto max-w-2xl space-y-2 p-6">
        {Array.from({ length: 8 }).map((_, index) => (
          <LoadingSkeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    )
  }

  if (alerts.length === 0) {
    return (
      <div className="mx-auto max-w-2xl p-6 text-sm text-muted-foreground">
        No alerts yet.
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl space-y-2 p-6">
      <h1 className="mb-2 text-xl font-semibold">Alerts</h1>
      {alerts.map((alert) => (
        <AlertRow key={alert.id} alert={alert} onMarkRead={markReadMutation.mutate} />
      ))}
    </div>
  )
}
