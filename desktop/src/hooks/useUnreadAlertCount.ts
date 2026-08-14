import { useQuery } from "@tanstack/react-query"
import * as alertsApi from "@/api/alerts"

// GET /alerts?unread=true already filters server-side — no need to port
// web's alertStore (which also tracks addAlert/markRead for a full
// AlertsPage desktop doesn't have yet, see components/layout/Sidebar.tsx's
// bell icon).
export function useUnreadAlertCount() {
  return useQuery({
    queryKey: ["alerts", "unread-count"],
    queryFn: () => alertsApi.getAlerts(true),
    select: (alerts) => alerts.length,
    refetchInterval: 30_000,
  })
}
