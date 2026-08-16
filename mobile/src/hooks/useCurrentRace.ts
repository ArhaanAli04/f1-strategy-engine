import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"

// Hand-written — mirrors web/src/hooks/useCurrentRace.ts exactly (pure
// react-query, no browser APIs).
const CURRENT_RACE_STALE_TIME_MS = 60_000

export function useCurrentRace() {
  return useQuery({
    queryKey: ["race", "current"],
    queryFn: () => raceApi.getCurrentRace(),
    staleTime: CURRENT_RACE_STALE_TIME_MS,
    retry: false,
  })
}
