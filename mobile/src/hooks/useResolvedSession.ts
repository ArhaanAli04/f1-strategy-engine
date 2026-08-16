import { useQuery } from "@tanstack/react-query"
import * as raceApi from "@/api/race"
import { useCurrentRace } from "@/hooks/useCurrentRace"

// Hand-written — mirrors web/src/hooks/useResolvedSession.ts exactly (pure
// react-query, no browser APIs).
const FALLBACK_STALE_TIME_MS = 5 * 60 * 1000
const RACE_LIST_PAGE_SIZE = 10

interface ResolvedSession {
  sessionId: string | null
  season: number | null
  isLive: boolean
  raceName: string | null
  raceDate: string | null
}

// Option 2: when there's no live race, fall back to the most recent
// *completed* race's Race session instead of leaving screens empty.
export function useResolvedSession(): ResolvedSession {
  const { data: currentRace, isFetched: currentFetched } = useCurrentRace()
  const currentRaceSession = currentRace?.sessions.find((s) => s.session_type === "R") ?? null
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
