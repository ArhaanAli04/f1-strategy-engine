import { useEffect } from "react"
import { useParams } from "react-router-dom"
import { CircuitMapPanel } from "@/components/circuit/CircuitMapPanel"
import { LapTimeChart } from "@/components/telemetry/LapTimeChart"
import { LiveTimingTower } from "@/components/telemetry/LiveTimingTower"
import { SectorHeatmap } from "@/components/telemetry/SectorHeatmap"
import { HistoricalDataBanner } from "@/components/shared/HistoricalDataBanner"
import { StrategyOverviewGrid } from "@/components/strategy/StrategyOverviewGrid"
import { UndercutThreatPanel } from "@/components/strategy/UndercutThreatPanel"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDrivers } from "@/hooks/useDrivers"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useSessionStore } from "@/stores/sessionStore"

export function RacePage() {
  const { sessionId: paramSessionId } = useParams<{ sessionId: string }>()
  // Falls back to the most recent completed race's session whenever this
  // page is reached without an explicit :sessionId (Option 2, same as
  // DriverPage) — the URL param still wins when present, since that's a
  // deliberate deep link to a specific session.
  const { sessionId: resolvedSessionId, isLive, raceName, raceDate } = useResolvedSession()
  const sessionId = paramSessionId ?? resolvedSessionId ?? undefined
  // Only show the "showing historical data" banner for the automatic
  // fallback — an explicit /race/:sessionId deep link is deliberate user
  // intent, not something to second-guess with this notice.
  const showHistoricalBanner = !paramSessionId && Boolean(sessionId) && !isLive
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
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {showHistoricalBanner && (
        <HistoricalDataBanner sessionId={sessionId} raceName={raceName} raceDate={raceDate} />
      )}
      <div className="flex flex-1 overflow-x-auto overflow-y-hidden">
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
    </div>
  )
}
