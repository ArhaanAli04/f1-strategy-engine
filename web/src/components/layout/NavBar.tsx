import { Bell, LogOut, Settings as SettingsIcon } from "lucide-react"
import { Link, NavLink, useLocation } from "react-router-dom"
import { useAuth } from "@/hooks/useAuth"
import { useAlertStore } from "@/stores/alertStore"
import { cn } from "@/lib/utils"
import { ROUTES } from "@/utils/constants"

// unreadCount is read straight from alertStore (populated by AlertsPage's
// fetch, not re-fetched here) — see alertStore.ts.
const NAV_LINKS = [
  { to: ROUTES.DASHBOARD, label: "Dashboard" },
  { to: ROUTES.SIMULATE, label: "Simulator" },
] as const

export function NavBar() {
  const location = useLocation()
  const unreadCount = useAlertStore((state) => state.unreadCount)
  // logout() only clears local auth state (and best-effort revokes the
  // token server-side) — no manual navigate needed, AuthGuard redirects to
  // /login on its own once isAuthenticated flips false.
  const { logout, isLoggingOut } = useAuth()

  return (
    <header className="flex h-12 flex-shrink-0 items-center justify-between border-b px-4">
      <div className="flex items-center gap-6">
        <span className="text-sm font-semibold">F1 Strategy Engine</span>
        <nav className="flex items-center gap-1">
          {NAV_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="flex items-center gap-1">
        <Link
          to={ROUTES.ALERTS}
          aria-label="Alerts"
          className="relative flex h-11 w-11 items-center justify-center rounded-md hover:bg-accent"
        >
          <Bell className="h-4 w-4" />
          {unreadCount > 0 && (
            <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-destructive-foreground">
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          )}
        </Link>
        <Link
          to={ROUTES.SETTINGS}
          state={{ backgroundLocation: location }}
          aria-label="Settings"
          className="flex h-11 w-11 items-center justify-center rounded-md hover:bg-accent"
        >
          <SettingsIcon className="h-4 w-4" />
        </Link>
        <button
          type="button"
          onClick={() => logout()}
          disabled={isLoggingOut}
          aria-label="Log out"
          className="flex h-11 w-11 items-center justify-center rounded-md hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          <LogOut className="h-4 w-4" />
        </button>
      </div>
    </header>
  )
}
