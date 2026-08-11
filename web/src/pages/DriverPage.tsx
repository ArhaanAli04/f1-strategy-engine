import { useMemo } from "react"
import { useParams } from "react-router-dom"
import { LapTimesChart } from "@/components/driver/LapTimesChart"
import { SectorComparison } from "@/components/driver/SectorComparison"
import { StyleRadar } from "@/components/driver/StyleRadar"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useCurrentRace } from "@/hooks/useCurrentRace"
import { useDriverSeasonStats } from "@/hooks/useDriverSeasonStats"
import { useDrivers } from "@/hooks/useDrivers"

const FALLBACK_TEAM_COLOR = "#6B7280"

// GET /races/current can 404 (off-season, no race today) — in that case
// there is no session to scope analysis/laps/sectors to, and the three
// panels below each render their own "no active session" empty state
// rather than erroring.
function useResolvedSessionId() {
  const { data: race } = useCurrentRace()
  return useMemo(() => {
    if (!race || race.sessions.length === 0) return { sessionId: null, season: race?.season ?? null }
    const raceSession = race.sessions.find((s) => s.session_type === "R")
    const latest = [...race.sessions].sort((a, b) => b.session_date.localeCompare(a.session_date))[0]
    return { sessionId: (raceSession ?? latest).id, season: race.season }
  }, [race])
}

export function DriverPage() {
  const { driverId } = useParams<{ driverId: string }>()
  const { data: drivers, isLoading: driversLoading } = useDrivers()
  const { sessionId, season } = useResolvedSessionId()

  const driver = drivers?.find((d) => d.id === driverId) ?? null
  const team = driver?.contracts[0]?.team ?? null
  const teamColor = team?.color_hex ?? FALLBACK_TEAM_COLOR

  const { data: seasonStats, isLoading: statsLoading } = useDriverSeasonStats(
    driver?.code ?? null,
    season ?? new Date().getFullYear(),
  )

  if (driversLoading) {
    return (
      <div className="h-full overflow-y-auto p-6">
        <div className="mx-auto max-w-4xl space-y-4">
          <LoadingSkeleton className="h-24 w-full" />
          <LoadingSkeleton className="h-72 w-full" />
        </div>
      </div>
    )
  }

  if (!driver) {
    return <div className="h-full overflow-y-auto p-6 text-sm text-muted-foreground">Driver not found.</div>
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <div className="overflow-hidden rounded-lg border">
          <div className="h-2" style={{ backgroundColor: teamColor }} />
          <div className="flex flex-wrap items-center justify-between gap-4 p-4">
            <div>
              <h1 className="text-2xl font-bold">{driver.full_name}</h1>
              <p className="text-sm text-muted-foreground">{team?.name ?? "No team"}</p>
            </div>
            {statsLoading ? (
              <LoadingSkeleton className="h-10 w-64" />
            ) : seasonStats ? (
              <div className="flex gap-6 text-center">
                <div>
                  <div className="text-lg font-bold tabular-nums">{seasonStats.wins}</div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Wins</div>
                </div>
                <div>
                  <div className="text-lg font-bold tabular-nums">{seasonStats.podiums}</div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Podiums</div>
                </div>
                <div>
                  <div className="text-lg font-bold tabular-nums">{seasonStats.points}</div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Points</div>
                </div>
              </div>
            ) : null}
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Driving Style</CardTitle>
          </CardHeader>
          <CardContent>
            <StyleRadar driverId={driver.id} sessionId={sessionId} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Lap Times by Compound</CardTitle>
          </CardHeader>
          <CardContent>
            <LapTimesChart sessionId={sessionId} driverId={driver.id} />
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
    </div>
  )
}
