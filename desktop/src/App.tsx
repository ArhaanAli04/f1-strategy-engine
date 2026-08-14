import { useState } from "react"
import { Sidebar } from "@/components/layout/Sidebar"
import { useAuth } from "@/hooks/useAuth"
import { useRaceContextBridge } from "@/hooks/useRaceContextBridge"
import { useTrayStatus } from "@/hooks/useTrayStatus"
import { useUnreadAlertCount } from "@/hooks/useUnreadAlertCount"
import { useUndercutNotifications } from "@/hooks/useUndercutNotifications"
import { AlertsPage } from "@/pages/AlertsPage"
import { DashboardPage } from "@/pages/DashboardPage"
import { DriverAnalyticsPage } from "@/pages/DriverAnalyticsPage"
import { LiveRacePage } from "@/pages/LiveRacePage"
import { LoginPage } from "@/pages/LoginPage"
import { RegisterPage } from "@/pages/RegisterPage"
import { SettingsPage } from "@/pages/SettingsPage"
import { SimulatorPage } from "@/pages/SimulatorPage"
import { useAuthStore } from "@/stores/authStore"

export type DesktopPage = "dashboard" | "simulator" | "driverAnalytics" | "liveRace" | "alerts" | "settings"

function AuthenticatedShell() {
  const { logout, isLoggingOut } = useAuth()
  const [page, setPage] = useState<DesktopPage>("dashboard")
  const { data: unreadAlertCount } = useUnreadAlertCount()

  useRaceContextBridge()
  useTrayStatus()
  useUndercutNotifications()

  return (
    <div className="flex h-screen">
      <Sidebar
        page={page}
        onNavigate={setPage}
        unreadAlertCount={unreadAlertCount ?? 0}
        onLogout={() => void logout()}
        isLoggingOut={isLoggingOut}
      />
      <div className="flex-1 overflow-hidden">
        {page === "dashboard" && <DashboardPage onNavigate={setPage} />}
        {page === "simulator" && <SimulatorPage />}
        {page === "driverAnalytics" && <DriverAnalyticsPage />}
        {page === "liveRace" && <LiveRacePage />}
        {page === "alerts" && <AlertsPage />}
        {page === "settings" && <SettingsPage />}
      </div>
    </div>
  )
}

function App() {
  const isAuthenticated = useAuthStore((state) => state.accessToken !== null)
  const [authPage, setAuthPage] = useState<"login" | "register">("login")

  if (isAuthenticated) return <AuthenticatedShell />

  return authPage === "login" ? (
    <LoginPage onNavigateToRegister={() => setAuthPage("register")} />
  ) : (
    <RegisterPage onNavigateToLogin={() => setAuthPage("login")} />
  )
}

export default App
