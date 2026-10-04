import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"

// Race detail rarely changes — matches the backend's own 86400s cache TTL
// for this same lookup (race_service.RACE_DETAIL_TTL_SECONDS).
const RACE_BY_SESSION_STALE_TIME_MS = 5 * 60 * 1000

// Resolves whichever session is actually being viewed (live, replay, or
// historical) to ITS OWN race + circuit — see CircuitMapPanel's Day 43 fix.
// Distinct from useUpcomingRace, which answers a different question ("what's
// coming up next on the calendar") that has nothing to do with sessionId.
export function useRaceBySession(sessionId: string | null) {
  return useQuery({
    queryKey: ["race", "by-session", sessionId],
    queryFn: () => raceApi.getRaceBySession(sessionId as string),
    enabled: Boolean(sessionId),
    staleTime: RACE_BY_SESSION_STALE_TIME_MS,
    retry: false,
  })
}
