import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"

// Hand-written — mirrors web/src/hooks/useUpcomingRace.ts exactly.
const UPCOMING_RACE_STALE_TIME_MS = 5 * 60 * 1000

export function useUpcomingRace() {
  return useQuery({
    queryKey: ["race", "upcoming"],
    queryFn: () => raceApi.getUpcomingRace(),
    staleTime: UPCOMING_RACE_STALE_TIME_MS,
    retry: false,
  })
}
