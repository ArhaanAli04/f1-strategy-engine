import { useReplayStatus } from "@/hooks/useDemoReplay"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useRaceContextStore } from "@/stores/raceContextStore"

interface RaceSession {
  sessionId: string | null
  // True only for the automatic session when it is a genuinely live race.
  isLive: boolean
  // The Dashboard's override is in use instead of the automatic session.
  isOverride: boolean
  // A Demo Replay is running and this is its session.
  isReplay: boolean
  // Known for the automatic session and a replay (an override is a bare id).
  raceName: string | null
  raceDate: string | null
}

// The session every desktop page shows (demo deployment Day 6b), first
// match wins:
// 1. a session id typed on the Dashboard as an override;
// 2. the Demo Replay that is running, so every page, the overlay and the
//    notifications follow it, and fall back to 3 when it stops (web instead
//    navigates its race page to the replay's session; desktop has no router);
// 3. the live race if there is one, otherwise the most recent completed race,
//    exactly like web's useResolvedSession.
// Each window works this out itself (the overlay too), so nothing has to be
// typed for any page to show data.
export function useRaceSession(): RaceSession {
  const sessionOverride = useRaceContextStore((state) => state.sessionOverride)
  const resolved = useResolvedSession()
  const { data: replay } = useReplayStatus()

  if (sessionOverride) {
    return {
      sessionId: sessionOverride,
      isLive: false,
      isOverride: true,
      isReplay: false,
      raceName: null,
      raceDate: null,
    }
  }
  if (replay?.running && replay.session_id) {
    return {
      sessionId: replay.session_id,
      isLive: false,
      isOverride: false,
      isReplay: true,
      raceName: replay.race_name ?? null,
      raceDate: null,
    }
  }
  return {
    sessionId: resolved.sessionId,
    isLive: resolved.isLive,
    isOverride: false,
    isReplay: false,
    raceName: resolved.raceName,
    raceDate: resolved.raceDate,
  }
}
