import { useQuery } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"

// Mirrors web/src/hooks/useLastIngestedSession.ts (2026-10-07, the Simulator's
// session when no race is live), without web's meta.silentOn404: mobile has
// no global error toast for it to silence (see mobile/src/README.md).
// The backend caches this for 24h (strategy_service.LAST_INGESTED_SESSION_TTL_SECONDS)
// — it only changes when new race data is ingested — so there's no value in
// refetching it on the client faster than that.
const STALE_TIME_MS = 60 * 60 * 1000

// enabled: only the Strategy Simulator's non-live mode needs this. 404 (a
// fresh DB with no ingested races) is a normal state, surfaced by the screen
// as "No ingested race available".
export function useLastIngestedSession(enabled: boolean) {
  return useQuery({
    queryKey: ["strategy", "last-ingested-session"],
    queryFn: strategyApi.getLastIngestedSession,
    enabled,
    staleTime: STALE_TIME_MS,
    retry: false,
  })
}
