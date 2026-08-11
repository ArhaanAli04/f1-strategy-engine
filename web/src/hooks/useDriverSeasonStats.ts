import { useQuery } from "@tanstack/react-query"
import * as ergastApi from "@/api/ergast"
import { getErgastDriverId } from "@/utils/ergastDriverIds"

// Season standings don't change mid-week between races — an hour-long
// staleTime avoids re-hitting the external Ergast mirror on every DriverPage
// mount, per the Day 29 spec.
const SEASON_STATS_STALE_TIME_MS = 60 * 60 * 1000

export interface DriverSeasonStats {
  wins: number
  podiums: number
  points: number
}

function computeStats(races: { Results: { position: string; points: string }[] }[]): DriverSeasonStats {
  let wins = 0
  let podiums = 0
  let points = 0
  for (const race of races) {
    const result = race.Results[0]
    if (!result) continue
    const position = Number(result.position)
    if (position === 1) wins += 1
    if (!Number.isNaN(position) && position <= 3) podiums += 1
    points += Number(result.points) || 0
  }
  return { wins, podiums, points }
}

export function useDriverSeasonStats(driverCode: string | null, season: number) {
  const ergastDriverId = driverCode ? getErgastDriverId(driverCode) : null

  return useQuery({
    queryKey: ["ergast", "driver-season-stats", ergastDriverId, season],
    queryFn: async () => {
      const races = await ergastApi.getDriverSeasonResults(ergastDriverId as string, season)
      return computeStats(races)
    },
    enabled: Boolean(ergastDriverId),
    staleTime: SEASON_STATS_STALE_TIME_MS,
    retry: false,
  })
}
