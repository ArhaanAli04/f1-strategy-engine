import { useReplayStatus } from "@/hooks/useDemoReplay"
import { useResolvedSession } from "@/hooks/useResolvedSession"

interface RaceSession {
  sessionId: string | null
  // True only for the automatic session when it is a genuinely live race.
  isLive: boolean
  // A Demo Replay is running and this is its session.
  isReplay: boolean
  // The replay's race name, or the automatic session's.
  raceName: string | null
  // Known for the automatic session only (a replay's status has no date).
  raceDate: string | null
}

// The session the race screens show (Live, Strategy, Driver Detail), first
// match wins (Day 6b-mobile CP2, 2026-10-09):
// 1. the Demo Replay that is running, so every tab follows it and falls back
//    to 2 when it stops;
// 2. the live race if there is one, otherwise the most recent completed race
//    (useResolvedSession, as web).
// Desktop's useRaceSession, without its Dashboard override (mobile has no
// session field). Web instead navigates its race page to the replay's
// session, which an app without URLs can't do.
export function useRaceSession(): RaceSession {
  const resolved = useResolvedSession()
  const { data: replay } = useReplayStatus()

  if (replay?.running && replay.session_id) {
    return {
      sessionId: replay.session_id,
      isLive: false,
      isReplay: true,
      raceName: replay.race_name ?? null,
      raceDate: null,
    }
  }
  return {
    sessionId: resolved.sessionId,
    isLive: resolved.isLive,
    isReplay: false,
    raceName: resolved.raceName,
    raceDate: resolved.raceDate,
  }
}
