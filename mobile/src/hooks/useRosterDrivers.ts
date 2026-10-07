import { useMemo } from "react"
import { useConstructorStandings } from "@/hooks/useConstructorStandings"
import { useDrivers } from "@/hooks/useDrivers"
import { isActiveDriver } from "@/utils/drivers"
import type { DriverResponse } from "@/types"

// Derived from the wall clock, as on web, so no season is hard-coded.
const CURRENT_YEAR = new Date().getFullYear()

// Copied from web's DriverRosterGrid: Ergast lists Racing Bulls as "rb",
// while seed_teams.py's constructor_id is "racing_bulls".
const ERGAST_CONSTRUCTOR_ID_ALIASES: Record<string, string> = {
  racing_bulls: "rb",
}

function resolveErgastConstructorId(constructorId: string | undefined): string {
  if (!constructorId) return ""
  return ERGAST_CONSTRUCTOR_ID_ALIASES[constructorId] ?? constructorId
}

// Web's sort, copied: by the team's constructor-standings position (stable,
// so teammates keep their order), teams missing from the standings last, and
// alphabetical by team name when the standings are empty or failed to load.
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

// The active drivers in web's roster order (web/src/components/dashboard/
// DriverRosterGrid.tsx), shared by Home's roster and the Drivers tab so both
// list drivers by the current constructor standings.
export function useRosterDrivers() {
  const { data: drivers, dataUpdatedAt, isLoading } = useDrivers()
  const { data: standings } = useConstructorStandings(CURRENT_YEAR)

  const positionByConstructorId = useMemo(() => {
    const map = new Map<string, number>()
    for (const entry of standings ?? []) {
      map.set(entry.Constructor.constructorId, Number(entry.position))
    }
    return map
  }, [standings])

  const rosterDrivers = useMemo(
    () => sortByConstructorStandings((drivers ?? []).filter(isActiveDriver), positionByConstructorId),
    [drivers, positionByConstructorId],
  )

  return { rosterDrivers, dataUpdatedAt, isLoading }
}
