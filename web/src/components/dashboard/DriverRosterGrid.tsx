import { useMemo } from "react"
import { Link } from "react-router-dom"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { useConstructorStandings } from "@/hooks/useConstructorStandings"
import { useDrivers } from "@/hooks/useDrivers"
import { ROUTES } from "@/utils/constants"
import type { DriverResponse } from "@/types"

const FALLBACK_TEAM_COLOR = "#6B7280"
const ROSTER_SKELETON_COUNT = 22
// No hardcoded season — derives from wall-clock so this keeps working in
// 2027, 2028, etc. without a code change.
const CURRENT_YEAR = new Date().getFullYear()

// GET /drivers returns every driver ever ingested (see
// backend/apis/v1/drivers.py — no active-roster filter param), including
// retired/historical drivers with no current-season contract. Only a driver
// with a real team assignment belongs on a "current roster" grid.
function isActiveDriver(driver: DriverResponse): boolean {
  const contract = driver.contracts[0]
  return Boolean(contract?.team_id) && Boolean(contract?.team?.name)
}

// Confirmed live against api.jolpi.ca/ergast/f1/2026/constructorStandings/:
// every 2026 team's Ergast constructorId matches backend/scripts/seed_teams.py's
// constructor_id verbatim (mercedes, ferrari, mclaren, red_bull, alpine,
// haas, audi, williams, aston_martin, cadillac) except Racing Bulls, which
// Ergast lists as "rb" rather than "racing_bulls". Aliased here rather than
// changing our own constructor_id convention, since that value is also used
// elsewhere (team-color lookups etc.) and isn't Ergast-specific.
const ERGAST_CONSTRUCTOR_ID_ALIASES: Record<string, string> = {
  racing_bulls: "rb",
}

function resolveErgastConstructorId(constructorId: string | undefined): string {
  if (!constructorId) return ""
  return ERGAST_CONSTRUCTOR_ID_ALIASES[constructorId] ?? constructorId
}

// Sorts by each driver's team's real constructor-standings position. A
// stable sort preserves each driver's existing relative order within the
// same team/position, per spec. Any team missing from the standings (e.g. a
// brand-new entrant Ergast hasn't listed yet) sorts after every known team.
// Falls back to alphabetical-by-team-name when the standings response is
// empty or the fetch failed outright — most commonly early in a season
// before any points-paying session has happened.
function sortByConstructorStandings(
  drivers: DriverResponse[],
  positionByConstructorId: Map<string, number>,
): DriverResponse[] {
  if (positionByConstructorId.size === 0) {
    return [...drivers].sort((a, b) => {
      const teamA = a.contracts[0]?.team?.name ?? ""
      const teamB = b.contracts[0]?.team?.name ?? ""
      return teamA.localeCompare(teamB)
    })
  }
  return [...drivers].sort((a, b) => {
    const idA = resolveErgastConstructorId(a.contracts[0]?.team?.constructor_id)
    const idB = resolveErgastConstructorId(b.contracts[0]?.team?.constructor_id)
    const positionA = positionByConstructorId.get(idA) ?? Infinity
    const positionB = positionByConstructorId.get(idB) ?? Infinity
    return positionA - positionB
  })
}

// 22 drivers with team colors, linking to DriverPage — not a season
// standings table, since no results/points table exists backend-side (see
// CLAUDE.md's data model notes). Grid order itself is real constructor
// standings, fetched separately from Ergast/Jolpica (see
// useConstructorStandings).
export function DriverRosterGrid() {
  const { data: drivers, isLoading } = useDrivers()
  const { data: standings } = useConstructorStandings(CURRENT_YEAR)

  const positionByConstructorId = useMemo(() => {
    const map = new Map<string, number>()
    for (const entry of standings ?? []) {
      map.set(entry.Constructor.constructorId, Number(entry.position))
    }
    return map
  }, [standings])

  const activeDrivers = useMemo(
    () => sortByConstructorStandings((drivers ?? []).filter(isActiveDriver), positionByConstructorId),
    [drivers, positionByConstructorId],
  )

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
        {Array.from({ length: ROSTER_SKELETON_COUNT }).map((_, index) => (
          <LoadingSkeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
      {activeDrivers.map((driver) => {
        const team = driver.contracts[0]?.team
        return (
          <Link
            key={driver.id}
            to={ROUTES.driver(driver.id)}
            className="flex items-center gap-2 overflow-hidden rounded-md border p-2 transition-colors hover:bg-accent"
          >
            <TeamLogo teamName={team?.name} teamColor={team?.color_hex ?? FALLBACK_TEAM_COLOR} />
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold">{driver.code}</span>
              <span className="block truncate text-xs text-muted-foreground">
                {driver.contracts[0]?.team?.name ?? "No team"}
              </span>
            </span>
          </Link>
        )
      })}
    </div>
  )
}
