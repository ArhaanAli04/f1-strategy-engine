import { Navigate, Route, Routes, useLocation, type Location } from "react-router-dom"
import { AuthGuard } from "@/components/auth/AuthGuard"
import { SettingsModal } from "@/components/settings/SettingsModal"
import { NotFoundPage } from "@/components/shared/NotFoundPage"
import { AlertsPage } from "@/pages/AlertsPage"
import { DashboardPage } from "@/pages/DashboardPage"
import { DriverPage } from "@/pages/DriverPage"
import { LoginPage } from "@/pages/LoginPage"
import { RaceLivePage } from "@/pages/RaceLivePage"
import { RacePage } from "@/pages/RacePage"
import { RaceStrategyPage } from "@/pages/RaceStrategyPage"
import { RegisterPage } from "@/pages/RegisterPage"
import { SimulatorPage } from "@/pages/SimulatorPage"
import { ROUTES } from "@/utils/constants"

function App() {
  const location = useLocation()
  const state = location.state as { backgroundLocation?: Location } | null

  // Settings always renders as an overlay (see SettingsModal) — reached
  // either via NavBar's Link (a real backgroundLocation: whatever page you
  // were on) or via a direct URL/refresh, which has no state to read, so it
  // falls back to Dashboard as the base layer underneath.
  const backgroundLocation =
    state?.backgroundLocation ??
    (location.pathname === ROUTES.SETTINGS ? ({ pathname: ROUTES.DASHBOARD } as Location) : undefined)

  return (
    <>
      <Routes location={backgroundLocation ?? location}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        <Route element={<AuthGuard />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          {/* No :sessionId — RacePage falls back to useResolvedSession's
              most-recent-completed-race session when there's no live one. */}
          <Route path="/race" element={<RacePage />} />
          <Route path="/race/:sessionId" element={<RacePage />} />
          <Route path="/race/:sessionId/strategy" element={<RaceStrategyPage />} />
          <Route path="/race/:sessionId/live" element={<RaceLivePage />} />
          <Route path="/drivers/:driverId" element={<DriverPage />} />
          <Route path="/simulate" element={<SimulatorPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
        </Route>

        <Route path="*" element={<NotFoundPage />} />
      </Routes>

      {/* Overlay layer — matches against the real location, not
          backgroundLocation, and only mounts when Settings is open.
          Not wrapped in AuthGuard: that would render a second NavBar shell
          behind the modal. SettingsModal enforces its own auth redirect. */}
      {backgroundLocation && (
        <Routes>
          <Route path={ROUTES.SETTINGS} element={<SettingsModal />} />
        </Routes>
      )}
    </>
  )
}

export default App
