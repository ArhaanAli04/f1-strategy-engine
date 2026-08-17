import { useQuery } from "@tanstack/react-query"
import * as ergastApi from "@/api/ergast"
import type { ErgastRace } from "@/api/ergast"
import { getErgastDriverId } from "@/utils/ergastDriverIds"

// Hand-written — mirrors web/src/hooks/useDriverSeasonStats.ts exactly (pure
// react-query + fetch, no browser APIs to adapt away).
const SEASON_STATS_STALE_TIME_MS = 60 * 60 * 1000

export interface DriverSeasonStats {
  wins: number
  podiums: number
  points: number
  wdcPosition: number | null
  lastWinCircuit: string | null
  lastPodiumCircuit: string | null
}

// Races arrive in round order (ascending) — the last match found while
// iterating is the most recent, no separate date sort needed.
function computeRaceStats(races: ErgastRace[]): Omit<DriverSeasonStats, "wdcPosition"> {
  let wins = 0
  let podiums = 0
  let points = 0
  let lastWinCircuit: string | null = null
  let lastPodiumCircuit: string | null = null

  for (const race of races) {
    const result = race.Results[0]
    if (!result) continue
    const position = Number(result.position)
    if (position === 1) {
      wins += 1
      lastWinCircuit = race.Circuit.circuitName
    }
    if (!Number.isNaN(position) && position <= 3) {
      podiums += 1
      lastPodiumCircuit = race.Circuit.circuitName
    }
    points += Number(result.points) || 0
  }

  return { wins, podiums, points, lastWinCircuit, lastPodiumCircuit }
}

export function useDriverSeasonStats(driverCode: string | null, season: number) {
  const ergastDriverId = driverCode ? getErgastDriverId(driverCode) : null

  return useQuery({
    queryKey: ["ergast", "driver-season-stats", ergastDriverId, season],
    queryFn: async (): Promise<DriverSeasonStats> => {
      const [races, standing] = await Promise.all([
        ergastApi.getDriverSeasonResults(ergastDriverId as string, season),
        ergastApi.getDriverStandings(ergastDriverId as string, season),
      ])
      return {
        ...computeRaceStats(races),
        wdcPosition: standing ? Number(standing.position) : null,
      }
    },
    enabled: Boolean(ergastDriverId),
    staleTime: SEASON_STATS_STALE_TIME_MS,
    retry: false,
  })
}
