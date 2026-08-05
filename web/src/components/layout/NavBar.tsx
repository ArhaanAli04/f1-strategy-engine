import { Bell } from "lucide-react"
import { Link } from "react-router-dom"
import { useAlertStore } from "@/stores/alertStore"
import { ROUTES } from "@/utils/constants"

// Deliberately minimal per Day 28 scope — just the title and the bell.
// Full navigation links land Day 29 once DashboardPage and the rest of the
// page set exist. unreadCount is read straight from alertStore (populated
// by AlertsPage's fetch, not re-fetched here) — see alertStore.ts.
export function NavBar() {
  const unreadCount = useAlertStore((state) => state.unreadCount)

  return (
    <header className="flex h-12 flex-shrink-0 items-center justify-between border-b px-4">
      <span className="text-sm font-semibold">F1 Strategy Engine</span>
      <Link to={ROUTES.ALERTS} className="relative flex h-8 w-8 items-center justify-center rounded-md hover:bg-accent">
        <Bell className="h-4 w-4" />
        {unreadCount > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-destructive-foreground">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </Link>
    </header>
  )
}
