import { ArrowLeft } from "lucide-react"
import { DriverRosterGrid } from "@/components/dashboard/DriverRosterGrid"
import { LapTimesChart } from "@/components/driver/LapTimesChart"
import { SectorComparison } from "@/components/driver/SectorComparison"
import { StyleRadar } from "@/components/driver/StyleRadar"
import { HistoricalDataBanner } from "@/components/shared/HistoricalDataBanner"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDrivers } from "@/hooks/useDrivers"
import { useDriverSeasonStats } from "@/hooks/useDriverSeasonStats"
import { broadcastRaceContext } from "@/hooks/useRaceContextBridge"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useRaceContextStore } from "@/stores/raceContextStore"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"

interface StatTileProps {
  value: number | string
  label: string
}

function StatTile({ value, label }: StatTileProps) {
  return (
    <div>
      <div className="text-lg font-bold tabular-nums">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  )
}

// Adapted from web's DriverPage.tsx. Reuses the same DriverRosterGrid as
// Dashboard (not a separate picker) when no driver is selected; clicking a
// card sets raceContextStore.driverId and this page switches straight to
// the detail view. "Back to all drivers" clears driverId back to null
// rather than navigating away — Dashboard's own grid/context panel are
// untouched by either. Session comes from raceContextStore too, replacing
// useCurrentRace's live-detection (same swap as SimulatorPage).
export function DriverAnalyticsPage() {
  const contextDriverId = useRaceContextStore((state) => state.driverId)
  const sessionId = useRaceContextStore((state) => state.sessionId)

  function handleSelectDriver(driverId: string) {
    broadcastRaceContext(sessionId, driverId)
  }

  function handleBackToRoster() {
    broadcastRaceContext(sessionId, null)
  }

  const { data: drivers, isLoading: driversLoading } = useDrivers()
  const driver = drivers?.find((d) => d.id === contextDriverId) ?? null
  const team = driver?.contracts[0]?.team ?? null
  const teamColor = team?.color_hex ?? FALLBACK_TEAM_COLOR

  const { data: seasonStats, isLoading: statsLoading } = useDriverSeasonStats(
    driver?.code ?? null,
    new Date().getFullYear(),
  )

  // Same Redis-liveness signal as LiveRacePage/CircuitMapPanel — no session
  // date is available to compare against for a manually-typed sessionId, so
  // "no live positions" stands in for "this is historical data" here too.
  const { data: gapsResponse } = useSessionGaps(sessionId)
  const showHistoricalBanner = Boolean(sessionId) && (gapsResponse?.gaps.length ?? 0) === 0

  if (driversLoading) {
    return (
      <div className="h-full overflow-y-auto p-6">
        <div className="mx-auto max-w-6xl space-y-4">
          <LoadingSkeleton className="h-24 w-full" />
          <LoadingSkeleton className="h-72 w-full" />
        </div>
      </div>
    )
  }

  if (!driver) {
    return (
      <div className="h-full overflow-y-auto p-6">
        <div className="mx-auto max-w-6xl space-y-4">
          <p className="text-sm text-muted-foreground">Select a driver below to view their analytics.</p>
          <DriverRosterGrid onSelectDriver={handleSelectDriver} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {showHistoricalBanner && sessionId && <HistoricalDataBanner sessionId={sessionId} />}
      <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-6xl space-y-4">
        <Button variant="outline" size="sm" onClick={handleBackToRoster}>
          <ArrowLeft className="h-4 w-4" />
          Back to all drivers
        </Button>

        <div className="overflow-hidden rounded-lg border">
          <div className="h-2" style={{ backgroundColor: teamColor }} />
          <div className="flex flex-wrap items-center justify-between gap-4 p-4">
            <div className="flex items-center gap-3">
              <TeamLogo teamName={team?.name} teamColor={teamColor} className="h-10 w-10" />
              <div>
                <h1 className="text-2xl font-bold">{driver.full_name}</h1>
                <p className="text-sm text-muted-foreground">{team?.name ?? "No team"}</p>
              </div>
            </div>
            {statsLoading ? (
              <LoadingSkeleton className="h-10 w-80" />
            ) : seasonStats ? (
              <div className="flex gap-6 text-center">
                <StatTile value={seasonStats.wins} label="Wins" />
                <StatTile value={seasonStats.podiums} label="Podiums" />
                <StatTile value={seasonStats.points} label="Points" />
                <StatTile value={seasonStats.wdcPosition ?? "—"} label="WDC Pos" />
              </div>
            ) : null}
          </div>
          {seasonStats && (seasonStats.lastWinCircuit || seasonStats.lastPodiumCircuit) && (
            <div className="flex flex-wrap gap-x-6 gap-y-1 border-t px-4 py-2 text-xs text-muted-foreground">
              {seasonStats.lastWinCircuit && (
                <span>
                  Last win: <span className="text-foreground">{seasonStats.lastWinCircuit}</span>
                </span>
              )}
              {seasonStats.lastPodiumCircuit && (
                <span>
                  Last podium: <span className="text-foreground">{seasonStats.lastPodiumCircuit}</span>
                </span>
              )}
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Driving Style</CardTitle>
            </CardHeader>
            <CardContent>
              <StyleRadar driverId={driver.id} sessionId={sessionId} driverCode={driver.code} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Sector Times vs. Team Average</CardTitle>
            </CardHeader>
            <CardContent>
              <SectorComparison sessionId={sessionId} driverId={driver.id} />
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Lap Times by Compound</CardTitle>
          </CardHeader>
          <CardContent>
            <LapTimesChart sessionId={sessionId} driverId={driver.id} />
          </CardContent>
        </Card>
      </div>
      </div>
    </div>
  )
}
