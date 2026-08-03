import { Navigate, Route, Routes } from "react-router-dom"
import { AuthGuard } from "@/components/auth/AuthGuard"
import { NotFoundPage } from "@/components/shared/NotFoundPage"
import { AlertsPage } from "@/pages/AlertsPage"
import { DashboardPage } from "@/pages/DashboardPage"
import { DriverPage } from "@/pages/DriverPage"
import { LoginPage } from "@/pages/LoginPage"
import { RaceLivePage } from "@/pages/RaceLivePage"
import { RacePage } from "@/pages/RacePage"
import { RaceStrategyPage } from "@/pages/RaceStrategyPage"
import { RegisterPage } from "@/pages/RegisterPage"
import { SettingsPage } from "@/pages/SettingsPage"
import { SimulatorPage } from "@/pages/SimulatorPage"

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      <Route element={<AuthGuard />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/race/:sessionId" element={<RacePage />} />
        <Route path="/race/:sessionId/strategy" element={<RaceStrategyPage />} />
        <Route path="/race/:sessionId/live" element={<RaceLivePage />} />
        <Route path="/drivers/:driverId" element={<DriverPage />} />
        <Route path="/simulate" element={<SimulatorPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>

      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}

export default App
