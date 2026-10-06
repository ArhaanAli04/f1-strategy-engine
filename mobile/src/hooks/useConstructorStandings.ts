import { useQuery } from "@tanstack/react-query"
import * as ergastApi from "@/api/ergast"

// Standings don't change mid-week between races — same hour-long staleTime
// rationale as useDriverSeasonStats.
const CONSTRUCTOR_STANDINGS_STALE_TIME_MS = 60 * 60 * 1000

export function useConstructorStandings(season: number) {
  return useQuery({
    queryKey: ["ergast", "constructor-standings", season],
    queryFn: () => ergastApi.getConstructorStandings(season),
    staleTime: CONSTRUCTOR_STANDINGS_STALE_TIME_MS,
    retry: false,
  })
}
