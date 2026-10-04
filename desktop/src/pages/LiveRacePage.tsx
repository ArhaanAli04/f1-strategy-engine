import { CircuitMapPanel } from "@/components/circuit/CircuitMapPanel"
import { ReplaySelectorPanel } from "@/components/demo/ReplaySelectorPanel"
import { HistoricalDataBanner } from "@/components/shared/HistoricalDataBanner"
import { LapTimeChart } from "@/components/telemetry/LapTimeChart"
import { LiveTimingTower } from "@/components/telemetry/LiveTimingTower"
import { SectorHeatmap } from "@/components/telemetry/SectorHeatmap"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { StrategyOverviewGrid } from "@/components/strategy/StrategyOverviewGrid"
import { UndercutThreatPanel } from "@/components/strategy/UndercutThreatPanel"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDrivers } from "@/hooks/useDrivers"
import { useRaceSession } from "@/hooks/useRaceSession"
import { useLiveRaceSelectionStore } from "@/stores/liveRaceSelectionStore"

// Adapted from web's RacePage.tsx: the session comes from useRaceSession (the
// live race, else the most recent completed one, unless overridden on the
// Dashboard) instead of a /race/:sessionId URL param — desktop has no
// router. "Selected driver" for cross-panel sync stays a page-local concept
// (liveRaceSelectionStore), separate from raceContextStore's "my own driver"
// (see that store's comment).
export function LiveRacePage() {
  const { sessionId, isLive, isOverride, isReplay, raceName, raceDate } = useRaceSession()
  const selectedDriverId = useLiveRaceSelectionStore((state) => state.selectedDriverId)
  const { data: drivers } = useDrivers()
  // As on web: only for the automatic fallback to a completed race. An
  // override is the user's deliberate choice, like web's /race/:sessionId,
  // and a running replay is announced by its own panel.
  const showHistoricalBanner = Boolean(sessionId) && !isLive && !isOverride && !isReplay

  // Always rendered, same as web's RacePage — every panel below already
  // handles no-session/no-live-data internally (empty rosters, disabled
  // queries via enabled: Boolean(sessionId), CircuitMapPanel's own NON-RACE/
  // FINISHED/UNKNOWN modes). LiveTimingTower/CircuitMapPanel/SectorHeatmap
  // take a non-nullable sessionId prop, so "" stands in for "none yet" —
  // the same fallback DashboardPage's CircuitMapPanel already uses.
  const selectedDriver = drivers?.find((driver) => driver.id === selectedDriverId) ?? null

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {showHistoricalBanner && sessionId && (
        <HistoricalDataBanner sessionId={sessionId} raceName={raceName} raceDate={raceDate} />
      )}
      <div className="flex flex-1 overflow-x-auto overflow-y-hidden">
      <aside className="flex w-60 flex-shrink-0 flex-col overflow-y-auto border-r">
        <div className="flex-shrink-0 border-b px-3 py-2 text-sm font-semibold">Live Timing</div>
        <LiveTimingTower sessionId={sessionId ?? ""} />
      </aside>

      <main className="flex flex-1 flex-col overflow-y-auto">
        <ReplaySelectorPanel sessionId={sessionId} isLive={isLive} />
        {/* A replay or a Dashboard override is a deliberately chosen session:
            the map shows that race's own circuit (web: an explicit
            /race/:sessionId). The automatic fallback keeps the upcoming-race
            countdown when nothing is live. */}
        <CircuitMapPanel sessionId={sessionId ?? ""} isExplicitSession={isOverride || isReplay} />

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
              <SectorHeatmap sessionId={sessionId ?? ""} />
            </CardContent>
          </Card>
        </div>
      </main>

      <aside className="flex w-80 flex-shrink-0 flex-col gap-4 overflow-y-auto border-l p-4">
        <PitWindowCard sessionId={sessionId} driverId={selectedDriverId} />
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
