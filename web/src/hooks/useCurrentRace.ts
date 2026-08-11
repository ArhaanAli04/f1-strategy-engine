import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"

// Matches race_service.py's CURRENT_RACE_NOT_FOUND_TTL_SECONDS (60s) — no
// point polling faster than the backend's own negative-result cache window.
const CURRENT_RACE_STALE_TIME_MS = 60_000

export function useCurrentRace() {
  return useQuery({
    queryKey: ["race", "current"],
    queryFn: () => raceApi.getCurrentRace(),
    staleTime: CURRENT_RACE_STALE_TIME_MS,
    retry: false,
    // 404 (no race currently in progress/upcoming today) is a normal state,
    // not a global error toast — see useUpcomingRace's identical convention.
    meta: { silentOn404: true },
  })
}
