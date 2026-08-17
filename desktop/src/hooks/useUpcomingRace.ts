import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"

const UPCOMING_RACE_STALE_TIME_MS = 5 * 60 * 1000

export function useUpcomingRace() {
  return useQuery({
    queryKey: ["race", "upcoming"],
    queryFn: () => raceApi.getUpcomingRace(),
    staleTime: UPCOMING_RACE_STALE_TIME_MS,
    retry: false,
    // 404 (season concluded, no remaining races) is a normal state
    // CircuitMapPanel renders directly, not a global error toast.
    meta: { silentOn404: true },
  })
}
