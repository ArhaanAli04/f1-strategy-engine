import { useQuery } from "@tanstack/react-query"
import { CloudRain, Flag, Timer, TrendingDown, Users, type LucideIcon } from "lucide-react"
import { Link } from "react-router-dom"
import * as alertsApi from "@/api/alerts"
import { DriverChip } from "@/components/shared/DriverChip"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ROUTES } from "@/utils/constants"
import { AlertType } from "@/types"

const RECENT_ALERTS_COUNT = 5

// Same icon mapping as AlertsPage — kept as a local copy rather than a
// shared export since this is the only other place it's used and the two
// components have no other coupling.
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
    day: "2-digit",
    month: "short",
  })
}

export function RecentAlertsFeed() {
  const { data, isLoading } = useQuery({
    queryKey: ["alerts", "list"],
    queryFn: () => alertsApi.getAlerts(),
  })

  const recentAlerts = (data ?? []).slice(0, RECENT_ALERTS_COUNT)

  return (
    <Card className="flex h-64 flex-col">
      <CardHeader className="flex-shrink-0 flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">Recent Alerts</CardTitle>
        <Link to={ROUTES.ALERTS} className="text-xs text-primary underline underline-offset-4">
          View all
        </Link>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 space-y-2 overflow-y-auto">
        {isLoading &&
          Array.from({ length: RECENT_ALERTS_COUNT }).map((_, index) => (
            <LoadingSkeleton key={index} className="h-14 w-full" />
          ))}
        {!isLoading && recentAlerts.length === 0 && (
          <p className="text-sm text-muted-foreground">No alerts yet.</p>
        )}
        {recentAlerts.map((alert) => {
          const Icon = ALERT_ICONS[alert.alert_type] ?? Flag
          return (
            <div key={alert.id} className="flex items-start gap-3 rounded-md border p-2.5">
              <Icon className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
              <div className="flex-1 space-y-1">
                <div className="flex items-center gap-2">
                  {alert.driver_id && <DriverChip driverId={alert.driver_id} />}
                  <span className="text-xs text-muted-foreground">
                    {formatTimestamp(alert.triggered_at)}
                  </span>
                </div>
                <p className="text-sm">{alert.message}</p>
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
