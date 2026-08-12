import { useEffect } from "react"
import { useParams } from "react-router-dom"
import { CircuitMapPanel } from "@/components/circuit/CircuitMapPanel"
import { LapTimeChart } from "@/components/telemetry/LapTimeChart"
import { LiveTimingTower } from "@/components/telemetry/LiveTimingTower"
import { SectorHeatmap } from "@/components/telemetry/SectorHeatmap"
import { StrategyOverviewGrid } from "@/components/strategy/StrategyOverviewGrid"
import { UndercutThreatPanel } from "@/components/strategy/UndercutThreatPanel"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDrivers } from "@/hooks/useDrivers"
import { useSessionStore } from "@/stores/sessionStore"

export function RacePage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const setSelectedSession = useSessionStore((state) => state.setSelectedSession)
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const { data: drivers } = useDrivers()

  useEffect(() => {
    setSelectedSession(sessionId ?? null)
    return () => setSelectedSession(null)
  }, [sessionId, setSelectedSession])

  if (!sessionId) {
    return <div className="p-6 text-sm text-muted-foreground">No session selected.</div>
  }

  const selectedDriver = drivers?.find((driver) => driver.id === selectedDriverId) ?? null

  return (
    <div className="flex h-full overflow-x-auto overflow-y-hidden bg-background">
      <aside className="flex w-60 flex-shrink-0 flex-col overflow-y-auto border-r">
        <div className="flex-shrink-0 border-b px-3 py-2 text-sm font-semibold">Live Timing</div>
        <LiveTimingTower sessionId={sessionId} />
      </aside>

      <main className="flex flex-1 flex-col overflow-y-auto">
        <CircuitMapPanel sessionId={sessionId} />

        <div className="flex flex-1 flex-col gap-4 p-4">
          <Card>
            <CardHeader>
              <CardTitle>Lap Times{selectedDriver ? ` — ${selectedDriver.code}` : ""}</CardTitle>
            </CardHeader>
            <CardContent>
              <LapTimeChart sessionId={sessionId} driverId={selectedDriverId} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Sector Times</CardTitle>
            </CardHeader>
            <CardContent>
              <SectorHeatmap sessionId={sessionId} />
            </CardContent>
          </Card>
        </div>
      </main>

      <aside className="flex w-80 flex-shrink-0 flex-col gap-4 overflow-y-auto border-l p-4">
        <UndercutThreatPanel sessionId={sessionId} driverId={selectedDriverId} />
        <div>
          <div className="mb-2 text-sm font-semibold">Strategy Wall</div>
          <StrategyOverviewGrid sessionId={sessionId} />
        </div>
      </aside>
    </div>
  )
}
