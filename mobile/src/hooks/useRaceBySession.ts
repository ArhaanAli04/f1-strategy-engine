import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"

// Race detail rarely changes — matches the backend's own 86400s cache TTL
// for this same lookup (race_service.RACE_DETAIL_TTL_SECONDS).
const RACE_BY_SESSION_STALE_TIME_MS = 5 * 60 * 1000

// Mirrors web/src/hooks/useRaceBySession.ts. The query options are exported
// as well (mobile only) so the Alerts tab can look up the race of every
// session its alerts came from with useQueries, sharing this hook's cache.
export function raceBySessionQueryOptions(sessionId: string | null) {
  return {
    queryKey: ["race", "by-session", sessionId] as const,
    queryFn: () => raceApi.getRaceBySession(sessionId as string),
    enabled: Boolean(sessionId),
    staleTime: RACE_BY_SESSION_STALE_TIME_MS,
    retry: false,
  }
}

// Resolves a session to its own race + circuit.
export function useRaceBySession(sessionId: string | null) {
  return useQuery(raceBySessionQueryOptions(sessionId))
}
