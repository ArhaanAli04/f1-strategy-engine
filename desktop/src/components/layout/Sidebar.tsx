import { Bell, FlaskConical, LayoutDashboard, LogOut, Settings as SettingsIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import type { DesktopPage } from "@/App"

interface NavItemProps {
  icon: React.ReactNode
  label: string
  isActive: boolean
  badge?: number
  onClick: () => void
}

function NavItem({ icon, label, isActive, badge, onClick }: NavItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "relative flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        isActive
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      <span className="relative flex h-4 w-4 flex-shrink-0 items-center justify-center">
        {icon}
        {badge !== undefined && badge > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[9px] font-semibold text-destructive-foreground">
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </span>
      {label}
    </button>
  )
}

interface SidebarProps {
  page: DesktopPage
  onNavigate: (page: DesktopPage) => void
  unreadAlertCount: number
  onLogout: () => void
  isLoggingOut: boolean
}

// Left sidebar, not web's top NavBar — standard desktop-app convention.
// Live Race and Driver Analytics are deliberately NOT nav items here (same
// as web's NavBar — they're only reachable via Dashboard's quick-access
// cards, see components/dashboard/QuickAccessCards.tsx).
export function Sidebar({ page, onNavigate, unreadAlertCount, onLogout, isLoggingOut }: SidebarProps) {
  return (
    <aside className="flex h-screen w-52 flex-shrink-0 flex-col border-r border-border bg-background px-3 py-4">
      <span className="mb-6 px-2 text-sm font-semibold">F1 Strategy Engine</span>

      <nav className="flex flex-col gap-1">
        <NavItem
          icon={<LayoutDashboard className="h-4 w-4" />}
          label="Dashboard"
          isActive={page === "dashboard"}
          onClick={() => onNavigate("dashboard")}
        />
        <NavItem
          icon={<FlaskConical className="h-4 w-4" />}
          label="Simulator"
          isActive={page === "simulator"}
          onClick={() => onNavigate("simulator")}
        />
      </nav>

      <div className="mt-auto flex flex-col gap-1">
        <NavItem
          icon={<Bell className="h-4 w-4" />}
          label="Alerts"
          isActive={page === "alerts"}
          badge={unreadAlertCount}
          onClick={() => onNavigate("alerts")}
        />
        <NavItem
          icon={<SettingsIcon className="h-4 w-4" />}
          label="Settings"
          isActive={page === "settings"}
          onClick={() => onNavigate("settings")}
        />
        <button
          type="button"
          onClick={onLogout}
          disabled={isLoggingOut}
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          <LogOut className="h-4 w-4" />
          Log out
        </button>
      </div>
    </aside>
  )
}
