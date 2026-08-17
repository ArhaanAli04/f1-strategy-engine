import { CircuitMapPanel } from "@/components/circuit/CircuitMapPanel"
import { DriverRosterGrid } from "@/components/dashboard/DriverRosterGrid"
import { QuickAccessCards } from "@/components/dashboard/QuickAccessCards"
import { RaceContextPanel } from "@/components/dashboard/RaceContextPanel"
import { broadcastRaceContext } from "@/hooks/useRaceContextBridge"
import { useRaceContextStore } from "@/stores/raceContextStore"
import type { DesktopPage } from "@/App"

interface DashboardPageProps {
  onNavigate: (page: DesktopPage) => void
}

export function DashboardPage({ onNavigate }: DashboardPageProps) {
  const sessionId = useRaceContextStore((state) => state.sessionId)

  function handleSelectDriver(driverId: string) {
    broadcastRaceContext(sessionId, driverId)
    onNavigate("driverAnalytics")
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <RaceContextPanel />
        {/* Always rendered — NON-RACE/FINISHED/UNKNOWN modes need no
            sessionId at all (useUpcomingRace fetches independently); only
            LIVE mode's position polling needs a real one, and
            useDriverPositions/useDriverCarNumbers are already no-ops
            (enabled: Boolean(sessionId)) against "". */}
        <CircuitMapPanel sessionId={sessionId ?? ""} />
        <QuickAccessCards onNavigate={onNavigate} />

        <div>
          <h2 className="mb-3 text-lg font-semibold">Driver Roster</h2>
          <DriverRosterGrid onSelectDriver={handleSelectDriver} />
        </div>
      </div>
    </div>
  )
}
