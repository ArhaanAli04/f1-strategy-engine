import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"
import { useCurrentRace } from "@/hooks/useCurrentRace"

// Fresh enough that a race that just went from "scheduled"/"in_progress" to
// "completed" is picked up within a few minutes, without refetching on
// every mount.
const FALLBACK_STALE_TIME_MS = 5 * 60 * 1000
// GET /races (no filters) returns newest-first by race_date — a handful of
// pages covers any scheduled-but-not-yet-run races that would otherwise
// sort ahead of the most recent actually-completed one.
const RACE_LIST_PAGE_SIZE = 10

interface ResolvedSession {
  sessionId: string | null
  season: number | null
  // false whenever sessionId came from the completed-race fallback rather
  // than a genuinely live/in-progress race — lets pages show a "showing
  // historical data" banner without a second fetch.
  isLive: boolean
  raceName: string | null
  raceDate: string | null
}

// Option 2: when there's no live race, fall back to the most recent
// *completed* race's Race session instead of leaving charts empty.
// RaceListResponse (from listRaces) has no sessions field, so finding that
// race's Race session id takes a second getRace call once the right race is
// identified from the list.
export function useResolvedSession(): ResolvedSession {
  const { data: currentRace, isFetched: currentFetched } = useCurrentRace()
  const currentRaceSession = currentRace?.sessions.find((s) => s.session_type === "R") ?? null
  // GET /races/current resolves to the next scheduled race well before it's
  // actually live (race_service.py's own docstring: "currently
  // active/upcoming" — same reasoning CircuitMapPanel/SimulatorPage's old
  // isLiveSessionMode already accounted for). A race with an R session whose
  // session_date hasn't arrived yet has no ingested data — treating it as
  // "live" 404s every session-scoped call. Confirmed live: currentRace
  // resolved to a Zandvoort R session 9 days out, sessionId got treated as
  // live, and GET /drivers/{id}/analysis?session_id=... 404'd.
  const liveSession =
    currentRaceSession && new Date(currentRaceSession.session_date).getTime() <= Date.now()
      ? currentRaceSession
      : null

  const fallbackQuery = useQuery({
    queryKey: ["race", "most-recent-completed"],
    queryFn: async () => {
      const list = await raceApi.listRaces({ page: 1, page_size: RACE_LIST_PAGE_SIZE })
      const mostRecentCompleted = list.items.find((race) => race.status === "completed")
      if (!mostRecentCompleted) return null
      return raceApi.getRace(mostRecentCompleted.id)
    },
    // Only needed once we know there's no live race — avoids firing this
    // extra list+detail pair on every mount while useCurrentRace is still
    // resolving its own (usually cache-hit) query.
    enabled: currentFetched && !liveSession,
    staleTime: FALLBACK_STALE_TIME_MS,
    retry: false,
  })

  if (liveSession) {
    return {
      sessionId: liveSession.id,
      season: currentRace?.season ?? null,
      isLive: true,
      raceName: currentRace?.event_name ?? null,
      raceDate: currentRace?.race_date ?? null,
    }
  }

  const fallbackRace = fallbackQuery.data
  const fallbackSession = fallbackRace?.sessions.find((s) => s.session_type === "R") ?? null

  return {
    sessionId: fallbackSession?.id ?? null,
    season: fallbackRace?.season ?? null,
    isLive: false,
    raceName: fallbackRace?.event_name ?? null,
    raceDate: fallbackRace?.race_date ?? null,
  }
}
