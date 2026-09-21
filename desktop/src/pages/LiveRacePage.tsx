import { CircuitMapPanel } from "@/components/circuit/CircuitMapPanel"
import { HistoricalDataBanner } from "@/components/shared/HistoricalDataBanner"
import { LapTimeChart } from "@/components/telemetry/LapTimeChart"
import { LiveTimingTower } from "@/components/telemetry/LiveTimingTower"
import { SectorHeatmap } from "@/components/telemetry/SectorHeatmap"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { StrategyOverviewGrid } from "@/components/strategy/StrategyOverviewGrid"
import { UndercutThreatPanel } from "@/components/strategy/UndercutThreatPanel"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDrivers } from "@/hooks/useDrivers"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useLiveRaceSelectionStore } from "@/stores/liveRaceSelectionStore"
import { useRaceContextStore } from "@/stores/raceContextStore"

// Adapted from web's RacePage.tsx: sessionId comes from raceContextStore
// (set on the Dashboard) instead of a /race/:sessionId URL param — desktop
// has no router. "Selected driver" for cross-panel sync stays a page-local
// concept (liveRaceSelectionStore), separate from raceContextStore's "my
// own driver" (see that store's comment).
export function LiveRacePage() {
  const sessionId = useRaceContextStore((state) => state.sessionId)
  const selectedDriverId = useLiveRaceSelectionStore((state) => state.selectedDriverId)
  const { data: drivers } = useDrivers()
  // Same Redis-liveness signal CircuitMapPanel uses (isLive = positions
  // present) — gaps is the cheaper of the two session-scoped queries
  // already fetched elsewhere on this page (LiveTimingTower/SectorHeatmap),
  // so react-query dedupes this call against theirs rather than adding a
  // new one.
  const { data: gapsResponse } = useSessionGaps(sessionId)
  const showHistoricalBanner = Boolean(sessionId) && (gapsResponse?.gaps.length ?? 0) === 0

  // Always rendered, same as web's RacePage — every panel below already
  // handles no-session/no-live-data internally (empty rosters, disabled
  // queries via enabled: Boolean(sessionId), CircuitMapPanel's own NON-RACE/
  // FINISHED/UNKNOWN modes). LiveTimingTower/CircuitMapPanel/SectorHeatmap
  // take a non-nullable sessionId prop, so "" stands in for "none yet" —
  // the same fallback DashboardPage's CircuitMapPanel already uses.
  const selectedDriver = drivers?.find((driver) => driver.id === selectedDriverId) ?? null

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {showHistoricalBanner && sessionId && <HistoricalDataBanner sessionId={sessionId} />}
      <div className="flex flex-1 overflow-x-auto overflow-y-hidden">
      <aside className="flex w-60 flex-shrink-0 flex-col overflow-y-auto border-r">
        <div className="flex-shrink-0 border-b px-3 py-2 text-sm font-semibold">Live Timing</div>
        <LiveTimingTower sessionId={sessionId ?? ""} />
      </aside>

      <main className="flex flex-1 flex-col overflow-y-auto">
        <CircuitMapPanel sessionId={sessionId ?? ""} />

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
