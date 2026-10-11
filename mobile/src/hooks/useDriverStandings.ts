import { useQuery } from "@tanstack/react-query"
import * as ergastApi from "@/api/ergast"

// Standings only change after a race, so an hour is plenty (same as
// useConstructorStandings).
const DRIVER_STANDINGS_STALE_TIME_MS = 60 * 60 * 1000

// The season's full drivers' championship in one Ergast request (mobile
// only, the Drivers tab's championship card).
export function useDriverStandings(season: number) {
  return useQuery({
    queryKey: ["ergast", "driver-standings", season],
    queryFn: () => ergastApi.getSeasonDriverStandings(season),
    staleTime: DRIVER_STANDINGS_STALE_TIME_MS,
    retry: false,
  })
}
